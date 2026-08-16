-- 021_tareas_tipos.sql -- tipos de actividad con jerarquía y flujo propio.
--
-- Antes `tareas.tipo` era texto libre y el flujo se resolvía siempre por espacio, así que
-- todas las actividades compartían el mismo ciclo de vida. Con esto cada tipo (épica,
-- tarea, subtarea, error…) declara su nivel jerárquico y puede apuntar a su propio flujo
-- con transiciones, condiciones y validaciones distintas.

CREATE TABLE IF NOT EXISTS tareas_tipos (
  id_tipo UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  clave TEXT NOT NULL UNIQUE,
  nombre TEXT NOT NULL,
  descripcion TEXT,
  icono TEXT,
  color TEXT,
  -- Nivel en el árbol de trabajo: una épica agrupa tareas y una tarea agrupa subtareas.
  jerarquia TEXT NOT NULL DEFAULT 'tarea' CHECK (jerarquia IN ('epica','tarea','subtarea')),
  id_flujo UUID REFERENCES tareas_flujos(id_flujo) ON DELETE SET NULL,
  orden INTEGER NOT NULL DEFAULT 0,
  activo BOOLEAN NOT NULL DEFAULT TRUE,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Posición en el lienzo del diseñador visual de flujos.
ALTER TABLE tareas_estados ADD COLUMN IF NOT EXISTS pos_x NUMERIC;
ALTER TABLE tareas_estados ADD COLUMN IF NOT EXISTS pos_y NUMERIC;

ALTER TABLE tareas ADD COLUMN IF NOT EXISTS id_tipo UUID REFERENCES tareas_tipos(id_tipo) ON DELETE SET NULL;
CREATE INDEX IF NOT EXISTS idx_tareas_tipo ON tareas(id_tipo);
CREATE INDEX IF NOT EXISTS idx_tareas_padre_estado ON tareas(id_padre) WHERE id_padre IS NOT NULL;

INSERT INTO tareas_tipos(clave,nombre,descripcion,icono,color,jerarquia,orden,id_flujo) VALUES
  ('epica','Épica','Objetivo amplio que agrupa varias tareas.','target','#7c5cff','epica',0,(SELECT id_flujo FROM tareas_flujos WHERE clave='basico')),
  ('tarea','Tarea','Trabajo concreto del laboratorio.','clipboard','#1f8f7a','tarea',1,(SELECT id_flujo FROM tareas_flujos WHERE clave='basico')),
  ('experimento','Experimento','Ensayo con protocolo y evidencia.','flask','#2f7fd4','tarea',2,(SELECT id_flujo FROM tareas_flujos WHERE clave='basico')),
  ('error','Incidencia','Contaminación, fallo de equipo o desviación.','alert','#c8452f','tarea',3,(SELECT id_flujo FROM tareas_flujos WHERE clave='basico')),
  ('mejora','Mejora','Ajuste de protocolo o de proceso.','sparkles','#d08700','tarea',4,(SELECT id_flujo FROM tareas_flujos WHERE clave='basico')),
  ('subtarea','Subtarea','Paso concreto dentro de una tarea.','check','#5b6b7a','subtarea',5,(SELECT id_flujo FROM tareas_flujos WHERE clave='basico'))
ON CONFLICT(clave) DO UPDATE SET
  nombre=EXCLUDED.nombre, descripcion=EXCLUDED.descripcion, icono=EXCLUDED.icono,
  color=EXCLUDED.color, jerarquia=EXCLUDED.jerarquia, orden=EXCLUDED.orden;

-- Las tareas existentes conservan su tipo textual; aquí se enlazan al catálogo.
UPDATE tareas t SET id_tipo = ti.id_tipo
FROM tareas_tipos ti
WHERE t.id_tipo IS NULL AND ti.clave = COALESCE(NULLIF(t.tipo,''),'tarea');
UPDATE tareas t SET id_tipo = (SELECT id_tipo FROM tareas_tipos WHERE clave='tarea')
WHERE t.id_tipo IS NULL;

-- Disposición inicial del flujo básico en el lienzo, en una fila legible.
UPDATE tareas_estados SET pos_x = 60 + orden * 230, pos_y = 120 WHERE pos_x IS NULL;

DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname='trg_tareas_tipos_updated_at') THEN
    CREATE TRIGGER trg_tareas_tipos_updated_at BEFORE UPDATE ON tareas_tipos
      FOR EACH ROW EXECUTE FUNCTION set_updated_at();
  END IF;
END $$;
