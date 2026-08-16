-- 015_user_profiles.sql
-- Perfil científico editable por cada usuario, separado de roles y seguridad.

ALTER TABLE usuarios_laboratorio
  ADD COLUMN IF NOT EXISTS portada_uri TEXT,
  ADD COLUMN IF NOT EXISTS institucion TEXT,
  ADD COLUMN IF NOT EXISTS departamento TEXT,
  ADD COLUMN IF NOT EXISTS grado_academico TEXT,
  ADD COLUMN IF NOT EXISTS linea_investigacion TEXT,
  ADD COLUMN IF NOT EXISTS biografia TEXT,
  ADD COLUMN IF NOT EXISTS orcid TEXT,
  ADD COLUMN IF NOT EXISTS telefono TEXT,
  ADD COLUMN IF NOT EXISTS ubicacion TEXT,
  ADD COLUMN IF NOT EXISTS enlace_personal TEXT;
