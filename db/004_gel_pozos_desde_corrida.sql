-- fago-api :: migración 004
-- Mejora el módulo de electroforesis para autogenerar los pozos desde una corrida de PCR.
--   - Estado de resultado por pozo con 5 valores (antes solo banda sí/no).
--   - Pozo de control positivo enlazable al inventario (caja de positivos).
--   - El gel queda enlazado a la corrida de PCR que lo originó.
-- Idempotente.

-- Estado de resultado por carril/pozo (pendiente / positivo / negativo / no_claro / no_revisado).
ALTER TABLE carriles_gel ADD COLUMN IF NOT EXISTS estado_resultado TEXT NOT NULL DEFAULT 'pendiente'
  CHECK (estado_resultado IN ('pendiente', 'positivo', 'negativo', 'no_claro', 'no_revisado'));

-- El pozo de control positivo puede apuntar al tubo de la caja de positivos.
ALTER TABLE carriles_gel ADD COLUMN IF NOT EXISTS id_control_positivo UUID
  REFERENCES controles_positivos(id_control_positivo);

-- El gel se enlaza de vuelta a la corrida de PCR que lo originó.
ALTER TABLE geles_electroforesis ADD COLUMN IF NOT EXISTS id_corrida_pcr UUID
  REFERENCES pcr_corridas(id_corrida_pcr) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS idx_geles_corrida ON geles_electroforesis(id_corrida_pcr);
CREATE INDEX IF NOT EXISTS idx_carriles_control_pos ON carriles_gel(id_control_positivo);
