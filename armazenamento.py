"""
Persistência JSON segura para múltiplos workers (gunicorn) e threads.

- trava(path): lock exclusivo entre processos (fcntl/msvcrt em <arquivo>.lock) + lock de thread.
  Reentrante na mesma thread.
- transacao(path, default): leitura-modificação-escrita atômica sob a trava.
- Escrita atômica: arquivo temporário + fsync + os.replace.
- JSON corrompido nunca é sobrescrito silenciosamente: uma cópia vai para quarentena
  (<arquivo>.corrompido-<data>) antes de qualquer nova gravação.

Ordem de travas quando mais de um arquivo é usado na mesma operação (evita deadlock):
viagem → historico → alerts → contacts.
"""
import contextlib, json, os, shutil, threading, time
from datetime import datetime

LOCK_TIMEOUT = 30  # segundos

_meta_lock = threading.Lock()
_thread_locks = {}
_local = threading.local()

if os.name == "nt":
    import msvcrt

    def _travar_fd(fd, timeout):
        limite = time.monotonic() + timeout
        while True:
            try:
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                return
            except OSError:
                if time.monotonic() >= limite:
                    raise TimeoutError("Tempo esgotado aguardando trava de arquivo")
                time.sleep(0.01)

    def _destravar_fd(fd):
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
else:
    import fcntl

    def _travar_fd(fd, timeout):
        limite = time.monotonic() + timeout
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return
            except BlockingIOError:
                if time.monotonic() >= limite:
                    raise TimeoutError("Tempo esgotado aguardando trava de arquivo")
                time.sleep(0.01)

    def _destravar_fd(fd):
        fcntl.flock(fd, fcntl.LOCK_UN)


def _lock_da_thread(path):
    with _meta_lock:
        return _thread_locks.setdefault(path, threading.RLock())


@contextlib.contextmanager
def trava(path, timeout=LOCK_TIMEOUT):
    path = os.path.abspath(path)
    tlock = _lock_da_thread(path)
    if not tlock.acquire(timeout=timeout):
        raise TimeoutError("Tempo esgotado aguardando trava de thread")
    estado = _local.__dict__.setdefault("travas", {})
    fd = None
    try:
        if path not in estado:
            fd = os.open(path + ".lock", os.O_RDWR | os.O_CREAT, 0o600)
            try:
                _travar_fd(fd, timeout)
            except BaseException:
                os.close(fd)
                raise
            estado[path] = 0
        estado[path] += 1
        try:
            yield
        finally:
            estado[path] -= 1
            if estado[path] == 0:
                del estado[path]
                if fd is not None:
                    _destravar_fd(fd)
                    os.close(fd)
    finally:
        tlock.release()


def _quarentena(path):
    destino = f"{path}.corrompido-{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}"
    shutil.copy2(path, destino)
    print(f"[DADOS] AVISO: JSON invalido preservado em quarentena: {os.path.basename(destino)}")


def _ler(path, default, estrito):
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, UnicodeDecodeError):
        _quarentena(path)
        return default
    except OSError:
        if estrito:   # nunca gravar por cima de um arquivo que não conseguimos ler
            raise
        return default


def _gravar(path, obj):
    tmp = f"{path}.{os.getpid()}.{threading.get_ident()}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())
    for tentativa in range(10):   # Windows: antivírus/indexador pode segurar o arquivo por instantes
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            if tentativa == 9:
                os.remove(tmp)
                raise
            time.sleep(0.05)


def ler_json(path, default):
    with trava(path):
        return _ler(path, default, estrito=False)


def gravar_json(path, obj):
    with trava(path):
        _gravar(path, obj)


def gravar_json_novo(path, obj):
    """Para arquivos de nome único (backups): escrita atômica, sem arquivo .lock."""
    _gravar(path, obj)


class _Transacao:
    def __init__(self, dados):
        self.dados = dados
        self.alterado = False

    def salvar(self, novos=None):
        if novos is not None:
            self.dados = novos
        self.alterado = True


@contextlib.contextmanager
def transacao(path, default):
    """Uso: with transacao(ARQ, []) as t: t.salvar(t.dados + [item]).
    Só grava se t.salvar() for chamado e nenhuma exceção ocorrer."""
    with trava(path):
        t = _Transacao(_ler(path, default, estrito=True))
        yield t
        if t.alterado:
            _gravar(path, t.dados)
