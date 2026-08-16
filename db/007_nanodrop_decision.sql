-- 007_nanodrop_decision.sql
-- Decisión de seguimiento del laboratorio sobre una lectura NanoDrop:
-- enviar el vial a PCR o marcarlo para repetir extracción. Se guarda como
-- JSONB con { estado, usuario, fecha, hora } para conservar quién y cuándo.
ALTER TABLE lecturas_nanodrop ADD COLUMN IF NOT EXISTS decision JSONB;
