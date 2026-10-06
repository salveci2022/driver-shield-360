"""
Agendador central de tarefas (substitui threading.Timer).

Cada tarefa tem um intervalo. A cada rodada, quem quiser executá-la precisa obter um
LEASE (PostgreSQL: tabela tarefas_agendadas; JSON: arquivo com trava). Assim, mesmo com
vários workers do gunicorn e várias instâncias, cada tarefa roda uma vez por intervalo.
As tarefas em si também são idempotentes (ex.: o disparo do DMS é um UPDATE atômico),
então uma execução duplicada nunca gera alerta duplicado.

Modos de execução:
  1. Embutido no servidor web (AGENDADOR_EMBUTIDO=1, padrão): uma thread por worker,
     iniciada na primeira requisição (seguro com fork do gunicorn).
  2. Processo dedicado (recomendado em produção, ex.: Render Background Worker):
        python agendador.py
     e AGENDADOR_EMBUTIDO=0 no serviço web.
"""
import os, signal, socket, threading, time


class Agendador:
    def __init__(self, obter_lease):
        self._obter_lease = obter_lease   # callable(nome, ttl_segundos, executor) -> bool
        self.tarefas = {}
        self._parar = threading.Event()
        self._thread = None
        self._thread_pid = None

    @property
    def executor(self):
        return f"{socket.gethostname()}:{os.getpid()}"

    def tarefa(self, nome, intervalo):
        def registrar(f):
            self.tarefas[nome] = {"funcao": f, "intervalo": float(intervalo), "proxima": 0.0}
            return f
        return registrar

    def executar_pendentes(self, forcar=False):
        """Executa as tarefas vencidas (localmente) cujo lease foi obtido. Retorna os nomes executados."""
        executadas = []
        agora = time.monotonic()
        for nome, t in self.tarefas.items():
            if not forcar and agora < t["proxima"]:
                continue
            t["proxima"] = agora + t["intervalo"]
            try:
                ttl = max(1.0, t["intervalo"] - 1.0)
                if not self._obter_lease(nome, ttl, self.executor):
                    continue
                t["funcao"]()
                executadas.append(nome)
            except Exception as e:   # uma tarefa com erro não derruba o agendador
                print(f"[AGENDADOR] Erro na tarefa {nome}: {type(e).__name__}")
        return executadas

    def _loop(self, passo):
        while not self._parar.wait(passo):
            self.executar_pendentes()

    def garantir_thread(self, passo=5.0):
        """Inicia (uma vez por processo) a thread do agendador embutido."""
        if self._thread_pid == os.getpid() and self._thread and self._thread.is_alive():
            return
        self._parar.clear()
        self._thread = threading.Thread(target=self._loop, args=(passo,), name="agendador", daemon=True)
        self._thread_pid = os.getpid()
        self._thread.start()

    def parar(self):
        self._parar.set()

    def rodar_para_sempre(self, passo=5.0):
        signal.signal(signal.SIGTERM, lambda *_: self._parar.set())
        signal.signal(signal.SIGINT, lambda *_: self._parar.set())
        print(f"[AGENDADOR] Iniciado ({len(self.tarefas)} tarefas, passo {passo}s)")
        self.executar_pendentes()
        self._loop(passo)
        print("[AGENDADOR] Encerrado")


if __name__ == "__main__":
    # Processo dedicado: nunca inicia a thread embutida neste processo
    os.environ["AGENDADOR_EMBUTIDO"] = "0"
    import app
    app.AGENDADOR.rodar_para_sempre(float(os.environ.get("AGENDADOR_PASSO", "5")))
