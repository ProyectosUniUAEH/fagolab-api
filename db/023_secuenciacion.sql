-- 023_secuenciacion.sql — el flujo continúa después del gel: secuenciación, archivos
-- FASTQ/FASTA y procedencia explícita del dato.
--
-- Por qué:
--   El laboratorio real llega hoy hasta electroforesis. Para validar el pipeline completo
--   (QC → BLAST → taxonomía → literatura → ficha) se trabaja además con secuencias
--   públicas de NCBI y con datos sintéticos. El sistema NUNCA debe mezclar esas tres
--   procedencias: cada secuenciación, cada archivo y cada evidencia declara de dónde viene.
--
--   origen_dato:
--     experimental  -> lo generó este laboratorio
--     publico_ncbi  -> descargado de NCBI (SRA / nuccore); se guarda el accession
--     sintetico     -> creado para probar el flujo

-- 1) La secuenciación deja de exigir una reacción PCR --------------------------------
-- Una secuenciación puede nacer de un carril de gel candidato, de un vial, o ser una
-- demostración con datos públicos que no tiene cadena experimental propia.
ALTER TABLE secuenciaciones ALTER COLUMN id_pcr_reaccion DROP NOT NULL;

ALTER TABLE secuenciaciones
  ADD COLUMN IF NOT EXISTS id_gel               UUID REFERENCES geles_electroforesis(id_gel) ON DELETE SET NULL,
  ADD COLUMN IF NOT EXISTS id_carril_gel        UUID REFERENCES carriles_gel(id_carril_gel) ON DELETE SET NULL,
  ADD COLUMN IF NOT EXISTS id_vial_adn          UUID REFERENCES viales_adn(id_vial_adn) ON DELETE SET NULL,
  ADD COLUMN IF NOT EXISTS origen_dato          TEXT NOT NULL DEFAULT 'experimental',
  ADD COLUMN IF NOT EXISTS fuente_externa       TEXT,
  ADD COLUMN IF NOT EXISTS accession_externo    TEXT,
  ADD COLUMN IF NOT EXISTS organismo_declarado  TEXT,
  ADD COLUMN IF NOT EXISTS plataforma           TEXT,
  ADD COLUMN IF NOT EXISTS tecnologia           TEXT,
  ADD COLUMN IF NOT EXISTS layout               TEXT,
  ADD COLUMN IF NOT EXISTS laboratorio          TEXT,
  ADD COLUMN IF NOT EXISTS fecha_secuenciacion  DATE,
  ADD COLUMN IF NOT EXISTS notas_procedencia    TEXT;

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'secuenciaciones_origen_dato_chk') THEN
    ALTER TABLE secuenciaciones ADD CONSTRAINT secuenciaciones_origen_dato_chk
      CHECK (origen_dato IN ('experimental','publico_ncbi','sintetico'));
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'secuenciaciones_estado_chk') THEN
    ALTER TABLE secuenciaciones ADD CONSTRAINT secuenciaciones_estado_chk
      CHECK (estado_secuenciacion IN ('pendiente','enviada','secuenciada','analizada','fallida'));
  END IF;
END $$;

COMMENT ON COLUMN secuenciaciones.origen_dato IS
  'experimental | publico_ncbi | sintetico. Trazabilidad: de dónde viene realmente la secuencia.';

CREATE INDEX IF NOT EXISTS idx_secuenciaciones_origen ON secuenciaciones(origen_dato, estado_secuenciacion);
CREATE INDEX IF NOT EXISTS idx_secuenciaciones_gel ON secuenciaciones(id_gel);

-- 2) Archivos de secuencia (FASTQ crudo y FASTA procesado son cosas distintas) --------
-- FASTQ = lecturas crudas del secuenciador (secuencia + calidad).
-- FASTA = secuencia ya procesada (consenso / contigs), sin calidad.
-- Las métricas de validación y QC se guardan como JSONB: son deterministas, sin IA.
CREATE TABLE IF NOT EXISTS archivos_secuencia (
  id_archivo_secuencia UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  id_secuenciacion UUID NOT NULL REFERENCES secuenciaciones(id_secuenciacion) ON DELETE CASCADE,
  formato TEXT NOT NULL CHECK (formato IN ('fasta','fastq')),
  rol TEXT NOT NULL DEFAULT 'consenso'
    CHECK (rol IN ('R1','R2','consenso','contigs','referencia','otro')),
  nombre_archivo TEXT NOT NULL,
  storage_uri TEXT NOT NULL,
  comprimido BOOLEAN NOT NULL DEFAULT FALSE,
  size_bytes BIGINT,
  sha256 TEXT,
  estado_validacion TEXT NOT NULL DEFAULT 'pendiente'
    CHECK (estado_validacion IN ('pendiente','valido','invalido')),
  semaforo_qc TEXT CHECK (semaforo_qc IN ('apta','revisar','insuficiente')),
  metricas JSONB NOT NULL DEFAULT '{}'::jsonb,
  hallazgos JSONB NOT NULL DEFAULT '[]'::jsonb,
  origen_dato TEXT NOT NULL DEFAULT 'experimental'
    CHECK (origen_dato IN ('experimental','publico_ncbi','sintetico')),
  subido_por TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (id_secuenciacion, rol, nombre_archivo)
);

COMMENT ON TABLE archivos_secuencia IS
  'Archivos crudos (FASTQ) y procesados (FASTA) de una secuenciación, con su validación y QC determinista.';

CREATE INDEX IF NOT EXISTS idx_archivos_secuencia_sec ON archivos_secuencia(id_secuenciacion, created_at);

-- 3) Evidencia externa: nada de lo que baja de internet se confunde con dato propio ---
-- Aquí aterrizan BLAST, taxonomía NCBI y literatura PubMed en las fases siguientes.
-- Se guarda siempre fecha de consulta y hash del contenido: una ficha generada por el
-- agente debe poder rastrearse hasta la evidencia exacta que la sustentó.
CREATE TABLE IF NOT EXISTS evidencias_externas (
  id_evidencia UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tipo TEXT NOT NULL
    CHECK (tipo IN ('blast','ncbi_nuccore','ncbi_taxonomy','pubmed','pmc','manual')),
  fuente TEXT NOT NULL
    CHECK (fuente IN ('experimental','ncbi','pubmed','sintetica','manual')),
  accession TEXT,
  pmid TEXT,
  titulo TEXT,
  url TEXT,
  contenido JSONB NOT NULL DEFAULT '{}'::jsonb,
  sha256 TEXT,
  fecha_consulta TIMESTAMPTZ NOT NULL DEFAULT now(),
  id_secuenciacion UUID REFERENCES secuenciaciones(id_secuenciacion) ON DELETE CASCADE,
  id_objeto UUID REFERENCES objetos_laboratorio(id_objeto) ON DELETE SET NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

COMMENT ON TABLE evidencias_externas IS
  'Evidencia recuperada de fuentes externas (NCBI, PubMed) con fecha de consulta y hash. Nunca se mezcla con datos experimentales.';

CREATE INDEX IF NOT EXISTS idx_evidencias_secuenciacion ON evidencias_externas(id_secuenciacion, tipo);
CREATE INDEX IF NOT EXISTS idx_evidencias_pmid ON evidencias_externas(pmid) WHERE pmid IS NOT NULL;
