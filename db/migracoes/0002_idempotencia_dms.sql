-- FASE 4 — Idempotência garantida pelo banco: no máximo UM alerta de Dead Man's Switch por viagem.
-- (O disparo já é atômico na aplicação; este índice é a segunda barreira.)
-- Somente comandos aditivos e idempotentes.
CREATE UNIQUE INDEX IF NOT EXISTS alertas_um_dms_por_viagem
    ON alertas (viagem_id) WHERE tipo = 'dead_mans_switch' AND viagem_id IS NOT NULL;
