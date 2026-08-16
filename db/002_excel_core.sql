-- 002_excel_core.sql
-- Ajustes mínimos para reflejar fielmente el NÚCLEO del Excel de muestreo.
-- La columna "Muestra" del Excel (ej. "M1 In b") codifica lote + órgano + RÉPLICA.
-- Guardamos la réplica explícita en la muestra biológica para no perder ese dato.

ALTER TABLE muestras_biologicas
  ADD COLUMN IF NOT EXISTS replica TEXT;

-- Catálogo completo de medios usados en el Excel (TSA, TCBS, Sangre, ADA, SyM, Mac C.).
-- Idempotente: no duplica si ya existen.
INSERT INTO medios_cultivo (nombre_medio, nombre_completo, tipo_medio, uso_principal)
VALUES
  ('TSA', 'Tryptic Soy Agar', 'nutritivo', 'Aislamiento bacteriano general'),
  ('TCBS', 'Thiosulfate Citrate Bile Salts Sucrose Agar', 'selectivo', 'Vibrio spp.'),
  ('Sangre', 'Agar Sangre', 'enriquecido', 'Hemólisis y bacterias exigentes'),
  ('ADA', 'Agar Dextrosa Almidón', 'nutritivo', 'Aislamiento general'),
  ('SyM', 'Sal y Manitol', 'selectivo', 'Staphylococcus spp.'),
  ('Mac C.', 'MacConkey', 'selectivo', 'Enterobacterias / Gram negativos')
ON CONFLICT (nombre_medio) DO NOTHING;

-- Un lote de medio disponible por cada medio (la científica no gestiona lotes en v1;
-- el backend resuelve el lote por defecto al sembrar una caja).
INSERT INTO lotes_medio_cultivo (id_medio_cultivo, codigo_lote_medio, estado_lote)
SELECT m.id_medio_cultivo, 'LM-' || m.nombre_medio || '-DEFAULT', 'disponible'
FROM medios_cultivo m
ON CONFLICT (codigo_lote_medio) DO NOTHING;

-- Ensayo PCR por defecto (16S rRNA) para identificación bacteriana.
INSERT INTO pcr_ensayos (codigo_ensayo, nombre_ensayo, gen_objetivo, tamano_amplicon_esperado_pb)
VALUES ('PCR-16S', 'Identificación 16S rRNA', '16S rRNA', 1500)
ON CONFLICT (codigo_ensayo) DO NOTHING;
