-- fago-api :: migración 003
-- Extiende la cadena molecular con tres piezas pedidas por la científica:
--   1. Caja de controles positivos: tubos que dan positivo, apartados en el lab,
--      reutilizables como control + de cada PCR. Se marca cuándo un vial se agota.
--   2. Corridas de PCR: memoria de qué muestras / lotes ya se amplificaron, con la
--      master mix usada (snapshot de la receta) y el plan de tubos.
--   3. Resultado por pozo de electroforesis que retroalimenta el resultado de cada
--      reacción (pozo 3 = muestra X = positivo -> la reacción queda marcada positiva).
-- Idempotente: usa IF NOT EXISTS / ADD COLUMN IF NOT EXISTS.

-- 1) Caja de controles positivos -------------------------------------------------------
CREATE TABLE IF NOT EXISTS controles_positivos (
  id_control_positivo UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  codigo_control TEXT NOT NULL UNIQUE,
  id_vial_adn UUID REFERENCES viales_adn(id_vial_adn),  -- opcional: vial real del sistema
  etiqueta TEXT NOT NULL,                                -- lo que dice el tubo físico
  organismo TEXT,                                        -- cepa / especie de referencia
  descripcion TEXT,
  concentracion_ng_ul NUMERIC(12,4),
  ubicacion TEXT,                                        -- posición en la caja de positivos
  estado_control TEXT NOT NULL DEFAULT 'disponible'
    CHECK (estado_control IN ('disponible', 'agotado')),
  veces_usado INTEGER NOT NULL DEFAULT 0,
  fecha_agregado DATE NOT NULL DEFAULT current_date,
  fecha_agotado DATE,
  observaciones TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 2) Corridas de PCR (una preparación de master mix = una sesión de trabajo) ------------
CREATE TABLE IF NOT EXISTS pcr_corridas (
  id_corrida_pcr UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  id_objeto UUID UNIQUE REFERENCES objetos_laboratorio(id_objeto),
  codigo_corrida TEXT NOT NULL UNIQUE,
  id_pcr_ensayo UUID REFERENCES pcr_ensayos(id_pcr_ensayo),
  id_control_positivo UUID REFERENCES controles_positivos(id_control_positivo),
  fecha_corrida DATE NOT NULL,
  responsable TEXT,
  volumen_final_ul NUMERIC(8,2),
  volumen_molde_ul NUMERIC(8,2),
  n_muestras INTEGER NOT NULL DEFAULT 0,
  n_reacciones INTEGER NOT NULL DEFAULT 0,
  usar_blanco BOOLEAN NOT NULL DEFAULT TRUE,
  usar_positivo BOOLEAN NOT NULL DEFAULT TRUE,
  margen_error INTEGER NOT NULL DEFAULT 1,
  receta JSONB NOT NULL DEFAULT '{}'::jsonb,             -- snapshot de la receta usada
  lotes TEXT,                                            -- resumen legible: "ZZ, M"
  estado_corrida TEXT NOT NULL DEFAULT 'preparada'
    CHECK (estado_corrida IN ('preparada', 'corrida', 'leida')),
  observaciones TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 3) Cada reacción se ata a su corrida, con nº de tubo y tipo --------------------------
-- El blanco y el control positivo SON tubos/reacciones pero no tienen vial del flujo,
-- así que id_vial_adn pasa a ser opcional.
ALTER TABLE pcr_reacciones ALTER COLUMN id_vial_adn DROP NOT NULL;
ALTER TABLE pcr_reacciones ADD COLUMN IF NOT EXISTS id_corrida_pcr UUID
  REFERENCES pcr_corridas(id_corrida_pcr) ON DELETE SET NULL;
ALTER TABLE pcr_reacciones ADD COLUMN IF NOT EXISTS numero_tubo INTEGER;
ALTER TABLE pcr_reacciones ADD COLUMN IF NOT EXISTS tipo_reaccion TEXT NOT NULL DEFAULT 'muestra'
  CHECK (tipo_reaccion IN ('muestra', 'blanco', 'positivo'));
ALTER TABLE pcr_reacciones ADD COLUMN IF NOT EXISTS codigo_muestra_visible TEXT;
ALTER TABLE pcr_reacciones ADD COLUMN IF NOT EXISTS lote TEXT;

CREATE INDEX IF NOT EXISTS idx_pcr_reacciones_corrida ON pcr_reacciones(id_corrida_pcr);
CREATE INDEX IF NOT EXISTS idx_pcr_corridas_fecha ON pcr_corridas(fecha_corrida DESC);

-- Triggers de updated_at
DROP TRIGGER IF EXISTS trg_controles_positivos_updated_at ON controles_positivos;
CREATE TRIGGER trg_controles_positivos_updated_at BEFORE UPDATE ON controles_positivos
FOR EACH ROW EXECUTE FUNCTION set_updated_at();

DROP TRIGGER IF EXISTS trg_pcr_corridas_updated_at ON pcr_corridas;
CREATE TRIGGER trg_pcr_corridas_updated_at BEFORE UPDATE ON pcr_corridas
FOR EACH ROW EXECUTE FUNCTION set_updated_at();
