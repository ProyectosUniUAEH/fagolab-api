-- 017_chat.sql -- conversaciones colaborativas. Todas las operaciones son idempotentes.
CREATE TABLE IF NOT EXISTS conversaciones (
  id_conversacion UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tipo TEXT NOT NULL CHECK (tipo IN ('directa', 'grupo')),
  nombre TEXT,
  clave_directa TEXT,
  creado_por UUID REFERENCES usuarios_laboratorio(id_usuario) ON DELETE SET NULL,
  ultimo_mensaje_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  CHECK ((tipo = 'directa' AND clave_directa IS NOT NULL) OR (tipo = 'grupo'))
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_conversaciones_clave_directa ON conversaciones(clave_directa) WHERE clave_directa IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_conversaciones_ultimo_mensaje ON conversaciones(ultimo_mensaje_at DESC NULLS LAST);

CREATE TABLE IF NOT EXISTS conversacion_miembros (
  id_conversacion UUID NOT NULL REFERENCES conversaciones(id_conversacion) ON DELETE CASCADE,
  id_usuario UUID NOT NULL REFERENCES usuarios_laboratorio(id_usuario) ON DELETE CASCADE,
  rol TEXT NOT NULL DEFAULT 'miembro' CHECK (rol IN ('propietario', 'administrador', 'miembro')),
  ultimo_leido_mensaje UUID, ultimo_leido_at TIMESTAMPTZ, silenciado_hasta TIMESTAMPTZ,
  fijada BOOLEAN NOT NULL DEFAULT FALSE, salido_at TIMESTAMPTZ, created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (id_conversacion, id_usuario)
);
CREATE INDEX IF NOT EXISTS idx_conversacion_miembros_usuario ON conversacion_miembros(id_usuario) WHERE salido_at IS NULL;

CREATE TABLE IF NOT EXISTS mensajes_conversacion (
  id_mensaje UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  id_conversacion UUID NOT NULL REFERENCES conversaciones(id_conversacion) ON DELETE CASCADE,
  id_autor UUID REFERENCES usuarios_laboratorio(id_usuario) ON DELETE SET NULL,
  tipo TEXT NOT NULL DEFAULT 'texto' CHECK (tipo IN ('texto', 'sistema', 'adjunto', 'tarea', 'agente')),
  cuerpo TEXT, adjuntos JSONB NOT NULL DEFAULT '[]'::jsonb, metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
  responde_a UUID REFERENCES mensajes_conversacion(id_mensaje) ON DELETE SET NULL,
  editado_at TIMESTAMPTZ, eliminado_at TIMESTAMPTZ, eliminado_por UUID REFERENCES usuarios_laboratorio(id_usuario) ON DELETE SET NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_mensajes_conversacion_fecha ON mensajes_conversacion(id_conversacion, created_at DESC);

DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'conversacion_miembros_ultimo_leido_fk') THEN
    ALTER TABLE conversacion_miembros ADD CONSTRAINT conversacion_miembros_ultimo_leido_fk FOREIGN KEY (ultimo_leido_mensaje) REFERENCES mensajes_conversacion(id_mensaje) ON DELETE SET NULL;
  END IF;
END $$;
CREATE TABLE IF NOT EXISTS mensaje_reacciones (
  id_mensaje UUID NOT NULL REFERENCES mensajes_conversacion(id_mensaje) ON DELETE CASCADE,
  id_usuario UUID NOT NULL REFERENCES usuarios_laboratorio(id_usuario) ON DELETE CASCADE,
  emoji TEXT NOT NULL CHECK (length(emoji) BETWEEN 1 AND 32), created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (id_mensaje, id_usuario, emoji)
);
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname='trg_conversaciones_updated_at') THEN CREATE TRIGGER trg_conversaciones_updated_at BEFORE UPDATE ON conversaciones FOR EACH ROW EXECUTE FUNCTION set_updated_at(); END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname='trg_mensajes_conversacion_updated_at') THEN CREATE TRIGGER trg_mensajes_conversacion_updated_at BEFORE UPDATE ON mensajes_conversacion FOR EACH ROW EXECUTE FUNCTION set_updated_at(); END IF;
END $$;
