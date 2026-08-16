-- 010_catalogos.sql
-- Catálogos de órganos y medios editables desde la app.
--
-- Antes de esta migración:
--   * Los ÓRGANOS no existían en la BD: eran texto libre en muestras_biologicas.organo_tejido
--     y una lista fija en el frontend (src/data/catalogs.ts). No se podían añadir nuevos.
--   * Los MEDIOS sí tenían tabla (medios_cultivo) pero sin el color que usa la interfaz,
--     así que el frontend mantenía una copia paralela que podía divergir.
--
-- Con esta migración ambos catálogos viven en la BD y la app puede ampliarlos.

CREATE TABLE IF NOT EXISTS organos (
  id_organo UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  codigo TEXT NOT NULL UNIQUE,
  nombre TEXT NOT NULL,
  orden INTEGER NOT NULL DEFAULT 100,
  activo BOOLEAN NOT NULL DEFAULT TRUE,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Los 9 órganos que ya usaba el frontend. El código entra en el código de la muestra
-- biológica (MB-PEZ-001-Hg), por eso se conservan tal cual, incluida la ñ de "Rñ".
INSERT INTO organos (codigo, nombre, orden)
VALUES
  ('Hg', 'Hígado', 10),
  ('Rñ', 'Riñón', 20),
  ('Br', 'Branquia', 30),
  ('In', 'Intestino', 40),
  ('Cr', 'Corazón', 50),
  ('Bz', 'Bazo', 60),
  ('Ms', 'Músculo', 70),
  ('Ln', 'Linfonodo', 80),
  ('Ls', 'Lesión externa', 90)
ON CONFLICT (codigo) DO NOTHING;

-- Color de la insignia del medio en la interfaz (vivía solo en el frontend).
ALTER TABLE medios_cultivo
  ADD COLUMN IF NOT EXISTS color TEXT,
  ADD COLUMN IF NOT EXISTS orden INTEGER NOT NULL DEFAULT 100;

UPDATE medios_cultivo SET color = '#0d9488', orden = 10 WHERE nombre_medio = 'TSA' AND color IS NULL;
UPDATE medios_cultivo SET color = '#2f6fed', orden = 20 WHERE nombre_medio = 'TCBS' AND color IS NULL;
UPDATE medios_cultivo SET color = '#e2574c', orden = 30 WHERE nombre_medio = 'Sangre' AND color IS NULL;
UPDATE medios_cultivo SET color = '#e0922f', orden = 40 WHERE nombre_medio = 'ADA' AND color IS NULL;
UPDATE medios_cultivo SET color = '#7c5cdb', orden = 50 WHERE nombre_medio = 'SyM' AND color IS NULL;
UPDATE medios_cultivo SET color = '#d4429a', orden = 60 WHERE nombre_medio = 'Mac C.' AND color IS NULL;

-- Cualquier medio creado al vuelo antes de esta migración se queda con un color neutro.
UPDATE medios_cultivo SET color = '#6b7d92' WHERE color IS NULL;
