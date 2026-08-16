-- fago-api :: esquema de base de datos (PostgreSQL 16)
-- Modelo de trazabilidad 1-N para fagoterapia en peces.
-- NUCLEO EXCEL (registro diario de la cientifica): peces -> muestras_biologicas (organo) ->
--   cajas_petri (medio) -> observaciones_caja_petri (descripcion de colonia).
-- El resto de columnas son OPCIONALES: permiten escalar (IA/MLOps) sin migraciones.
-- Generado desde el modelo ER de referencia. Versionar cambios como db/0NN_*.sql.

CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE OR REPLACE FUNCTION set_updated_at()
RETURNS TRIGGER AS $$
BEGIN
  NEW.updated_at = now();
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TABLE proyectos (
  id_proyecto UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  codigo_proyecto TEXT NOT NULL UNIQUE,
  nombre TEXT NOT NULL,
  objetivo TEXT,
  especie_objetivo TEXT,
  responsable_principal TEXT,
  institucion TEXT,
  fecha_inicio DATE,
  estado TEXT NOT NULL DEFAULT 'activo',
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE usuarios_laboratorio (
  id_usuario UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  nombre TEXT NOT NULL,
  rol TEXT,
  correo TEXT UNIQUE,
  activo BOOLEAN NOT NULL DEFAULT TRUE,
  password_hash TEXT,
  estado_cuenta TEXT NOT NULL DEFAULT 'pendiente'
    CHECK (estado_cuenta IN ('pendiente', 'activa', 'suspendida')),
  es_superadmin BOOLEAN NOT NULL DEFAULT FALSE,
  debe_cambiar_password BOOLEAN NOT NULL DEFAULT FALSE,
  cargo TEXT,
  avatar_uri TEXT,
  portada_uri TEXT,
  institucion TEXT,
  departamento TEXT,
  grado_academico TEXT,
  linea_investigacion TEXT,
  biografia TEXT,
  orcid TEXT,
  telefono TEXT,
  ubicacion TEXT,
  enlace_personal TEXT,
  ultimo_login_at TIMESTAMPTZ,
  ultima_actividad_at TIMESTAMPTZ,
  intentos_fallidos INTEGER NOT NULL DEFAULT 0 CHECK (intentos_fallidos >= 0),
  bloqueado_hasta TIMESTAMPTZ,
  aprobado_at TIMESTAMPTZ,
  aprobado_por UUID REFERENCES usuarios_laboratorio(id_usuario) ON DELETE SET NULL,
  password_cambiado_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE roles_acceso (
  id_rol UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  clave TEXT NOT NULL UNIQUE,
  nombre TEXT NOT NULL,
  descripcion TEXT,
  es_sistema BOOLEAN NOT NULL DEFAULT FALSE,
  activo BOOLEAN NOT NULL DEFAULT TRUE,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE permisos_acceso (
  id_permiso UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  clave TEXT NOT NULL UNIQUE,
  modulo TEXT NOT NULL,
  recurso TEXT NOT NULL,
  accion TEXT NOT NULL,
  descripcion TEXT,
  tipo TEXT NOT NULL DEFAULT 'accion'
    CHECK (tipo IN ('vista', 'accion', 'endpoint', 'sistema')),
  ruta_frontend TEXT,
  metodo_http TEXT,
  patron_endpoint TEXT,
  metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
  nivel_riesgo TEXT NOT NULL DEFAULT 'normal'
    CHECK (nivel_riesgo IN ('bajo', 'normal', 'alto', 'critico')),
  activo BOOLEAN NOT NULL DEFAULT TRUE,
  es_sistema BOOLEAN NOT NULL DEFAULT TRUE,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE roles_permisos (
  id_rol UUID NOT NULL REFERENCES roles_acceso(id_rol) ON DELETE CASCADE,
  id_permiso UUID NOT NULL REFERENCES permisos_acceso(id_permiso) ON DELETE CASCADE,
  asignado_por UUID REFERENCES usuarios_laboratorio(id_usuario) ON DELETE SET NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (id_rol, id_permiso)
);

-- Permisos que el sistema ya ofreció automáticamente a un rol de sistema. Hace que la
-- concesión inicial sea "una vez por permiso" y no "una vez por rol", para que un permiso
-- nuevo llegue al rol sin volver a conceder lo que una administradora revocó a propósito.
CREATE TABLE roles_permisos_semilla (
  id_rol UUID NOT NULL REFERENCES roles_acceso(id_rol) ON DELETE CASCADE,
  clave_permiso TEXT NOT NULL,
  otorgado_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (id_rol, clave_permiso)
);

CREATE TABLE usuarios_roles (
  id_usuario UUID NOT NULL REFERENCES usuarios_laboratorio(id_usuario) ON DELETE CASCADE,
  id_rol UUID NOT NULL REFERENCES roles_acceso(id_rol) ON DELETE CASCADE,
  asignado_por UUID REFERENCES usuarios_laboratorio(id_usuario) ON DELETE SET NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (id_usuario, id_rol)
);

CREATE TABLE grupos_acceso (
  id_grupo UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  clave TEXT NOT NULL UNIQUE,
  nombre TEXT NOT NULL,
  descripcion TEXT,
  activo BOOLEAN NOT NULL DEFAULT TRUE,
  creado_por UUID REFERENCES usuarios_laboratorio(id_usuario) ON DELETE SET NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE grupos_miembros (
  id_grupo UUID NOT NULL REFERENCES grupos_acceso(id_grupo) ON DELETE CASCADE,
  id_usuario UUID NOT NULL REFERENCES usuarios_laboratorio(id_usuario) ON DELETE CASCADE,
  asignado_por UUID REFERENCES usuarios_laboratorio(id_usuario) ON DELETE SET NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (id_grupo, id_usuario)
);

CREATE TABLE grupos_roles (
  id_grupo UUID NOT NULL REFERENCES grupos_acceso(id_grupo) ON DELETE CASCADE,
  id_rol UUID NOT NULL REFERENCES roles_acceso(id_rol) ON DELETE CASCADE,
  asignado_por UUID REFERENCES usuarios_laboratorio(id_usuario) ON DELETE SET NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (id_grupo, id_rol)
);

CREATE TABLE usuarios_permisos (
  id_usuario UUID NOT NULL REFERENCES usuarios_laboratorio(id_usuario) ON DELETE CASCADE,
  id_permiso UUID NOT NULL REFERENCES permisos_acceso(id_permiso) ON DELETE CASCADE,
  efecto TEXT NOT NULL CHECK (efecto IN ('allow', 'deny')),
  asignado_por UUID REFERENCES usuarios_laboratorio(id_usuario) ON DELETE SET NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (id_usuario, id_permiso)
);

CREATE TABLE grupos_permisos (
  id_grupo UUID NOT NULL REFERENCES grupos_acceso(id_grupo) ON DELETE CASCADE,
  id_permiso UUID NOT NULL REFERENCES permisos_acceso(id_permiso) ON DELETE CASCADE,
  efecto TEXT NOT NULL CHECK (efecto IN ('allow', 'deny')),
  asignado_por UUID REFERENCES usuarios_laboratorio(id_usuario) ON DELETE SET NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (id_grupo, id_permiso)
);

CREATE TABLE sesiones_usuario (
  id_sesion UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  id_usuario UUID NOT NULL REFERENCES usuarios_laboratorio(id_usuario) ON DELETE CASCADE,
  refresh_token_hash CHAR(64) NOT NULL UNIQUE,
  familia_token UUID NOT NULL DEFAULT gen_random_uuid(),
  emitido_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  expira_at TIMESTAMPTZ NOT NULL,
  ultima_actividad_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  revocado_at TIMESTAMPTZ,
  motivo_revocacion TEXT,
  ip TEXT,
  user_agent TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE objetos_laboratorio (
  id_objeto UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tipo_objeto TEXT NOT NULL CHECK (
    tipo_objeto IN (
      'recepcion_lote',
      'pez',
      'muestra_biologica',
      'caja_petri',
      'colonia',
      'subcultivo_petri',
      'aislamiento_bacteriano',
      'stock_bacteriano',
      'extraccion_adn',
      'vial_adn',
      'pcr_reaccion',
      'gel_electroforesis',
      'secuenciacion',
      'fago',
      'otro'
    )
  ),
  codigo TEXT NOT NULL UNIQUE,
  nombre TEXT,
  estado TEXT NOT NULL DEFAULT 'activo',
  metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE recepciones_lote (
  id_recepcion UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  id_objeto UUID UNIQUE REFERENCES objetos_laboratorio(id_objeto),
  codigo_recepcion TEXT NOT NULL UNIQUE,
  id_proyecto UUID NOT NULL REFERENCES proyectos(id_proyecto),
  fecha_recepcion DATE NOT NULL,
  hora_recepcion TIME,
  responsable_recepcion TEXT,
  cantidad_peces_declarada INTEGER CHECK (cantidad_peces_declarada IS NULL OR cantidad_peces_declarada >= 0),
  especie_reportada TEXT,
  nombre_cientifico_reportado TEXT,
  origen_tipo TEXT,
  origen_nombre TEXT,
  ubicacion_origen TEXT,
  estanque_origen TEXT,
  lote_productivo TEXT,
  sistema_cultivo TEXT,
  motivo_envio TEXT,
  descripcion_caso TEXT,
  condicion_transporte TEXT,
  temperatura_transporte_c NUMERIC(5,2),
  tiempo_transporte_horas NUMERIC(6,2),
  estado_general_lote TEXT,
  observaciones_lote TEXT,
  estado_registro TEXT NOT NULL DEFAULT 'abierto',
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE peces (
  id_pez UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  id_objeto UUID NOT NULL UNIQUE REFERENCES objetos_laboratorio(id_objeto),
  codigo_pez TEXT NOT NULL UNIQUE,
  id_recepcion UUID NOT NULL REFERENCES recepciones_lote(id_recepcion) ON DELETE RESTRICT,
  numero_pez_en_lote INTEGER NOT NULL CHECK (numero_pez_en_lote > 0),
  especie_observada TEXT,
  nombre_cientifico_observado TEXT,
  estado_al_recibir TEXT,
  sexo TEXT,
  etapa_productiva TEXT,
  peso_g NUMERIC(10,3),
  longitud_total_cm NUMERIC(10,3),
  longitud_estandar_cm NUMERIC(10,3),
  condicion_corporal TEXT,
  coloracion_anormal TEXT,
  lesiones_externas TEXT,
  branquias_observacion TEXT,
  ojos_observacion TEXT,
  piel_observacion TEXT,
  aletas_observacion TEXT,
  abdomen_observacion TEXT,
  signos_clinicos_resumen TEXT,
  diagnostico_presuntivo TEXT,
  prioridad_procesamiento TEXT,
  requiere_procesamiento BOOLEAN NOT NULL DEFAULT TRUE,
  estado_flujo TEXT NOT NULL DEFAULT 'registrado',
  observaciones TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (id_recepcion, numero_pez_en_lote)
);

CREATE TABLE muestras_biologicas (
  id_muestra_biologica UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  id_objeto UUID NOT NULL UNIQUE REFERENCES objetos_laboratorio(id_objeto),
  codigo_muestra_biologica TEXT NOT NULL UNIQUE,
  id_pez UUID NOT NULL REFERENCES peces(id_pez) ON DELETE RESTRICT,
  tipo_muestra TEXT NOT NULL,
  organo_tejido TEXT NOT NULL,
  lado_ubicacion TEXT,
  descripcion_lesion TEXT,
  metodo_toma TEXT,
  fecha_toma DATE NOT NULL,
  hora_toma TIME,
  responsable_toma TEXT,
  condicion_muestra TEXT,
  observaciones TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE medios_cultivo (
  id_medio_cultivo UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  nombre_medio TEXT NOT NULL UNIQUE,
  nombre_completo TEXT,
  tipo_medio TEXT,
  uso_principal TEXT,
  fabricante TEXT,
  descripcion TEXT,
  activo BOOLEAN NOT NULL DEFAULT TRUE
);

CREATE TABLE lotes_medio_cultivo (
  id_lote_medio UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  id_medio_cultivo UUID NOT NULL REFERENCES medios_cultivo(id_medio_cultivo),
  codigo_lote_medio TEXT NOT NULL UNIQUE,
  fecha_preparacion DATE,
  responsable_preparacion TEXT,
  cantidad_cajas_preparadas INTEGER CHECK (cantidad_cajas_preparadas IS NULL OR cantidad_cajas_preparadas >= 0),
  cantidad_cajas_disponibles INTEGER CHECK (cantidad_cajas_disponibles IS NULL OR cantidad_cajas_disponibles >= 0),
  esterilizacion TEXT,
  temperatura_esterilizacion_c NUMERIC(6,2),
  tiempo_esterilizacion_min NUMERIC(8,2),
  fecha_caducidad DATE,
  estado_lote TEXT NOT NULL DEFAULT 'disponible',
  observaciones TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE cajas_petri (
  id_caja_petri UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  id_objeto UUID NOT NULL UNIQUE REFERENCES objetos_laboratorio(id_objeto),
  codigo_caja_petri TEXT NOT NULL UNIQUE,
  id_muestra_biologica UUID NOT NULL REFERENCES muestras_biologicas(id_muestra_biologica) ON DELETE RESTRICT,
  id_lote_medio UUID NOT NULL REFERENCES lotes_medio_cultivo(id_lote_medio),
  numero_caja INTEGER NOT NULL CHECK (numero_caja > 0),
  fecha_siembra DATE NOT NULL,
  hora_siembra TIME,
  responsable_siembra TEXT,
  metodo_siembra TEXT,
  instrumento_siembra TEXT,
  temperatura_incubacion_c NUMERIC(5,2),
  tiempo_incubacion_horas NUMERIC(8,2),
  condicion_incubacion TEXT,
  fecha_ingreso_incubadora DATE,
  hora_ingreso_incubadora TIME,
  fecha_salida_incubadora DATE,
  hora_salida_incubadora TIME,
  estado_caja TEXT NOT NULL DEFAULT 'sembrada',
  resultado_preliminar TEXT,
  trazabilidad JSONB,
  observaciones TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (id_muestra_biologica, id_lote_medio, numero_caja)
);

CREATE TABLE observaciones_caja_petri (
  id_observacion_caja UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  id_caja_petri UUID NOT NULL REFERENCES cajas_petri(id_caja_petri) ON DELETE CASCADE,
  fecha_observacion DATE NOT NULL,
  hora_observacion TIME,
  horas_incubacion NUMERIC(8,2),
  responsable_observacion TEXT,
  hay_crecimiento BOOLEAN,
  cantidad_crecimiento TEXT,
  numero_morfotipos INTEGER CHECK (numero_morfotipos IS NULL OR numero_morfotipos >= 0),
  morfologia_colonial TEXT,
  color_colonias TEXT,
  forma_colonias TEXT,
  borde_colonias TEXT,
  elevacion_colonias TEXT,
  tamano_colonias_mm NUMERIC(8,3),
  olor TEXT,
  pigmentacion TEXT,
  hemolisis TEXT,
  contaminacion_visible BOOLEAN,
  calidad_aislamiento TEXT,
  accion_recomendada TEXT,
  observaciones TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE colonias_seleccionadas (
  id_colonia UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  id_objeto UUID NOT NULL UNIQUE REFERENCES objetos_laboratorio(id_objeto),
  codigo_colonia TEXT NOT NULL UNIQUE,
  id_caja_petri UUID NOT NULL REFERENCES cajas_petri(id_caja_petri) ON DELETE RESTRICT,
  id_observacion_caja UUID REFERENCES observaciones_caja_petri(id_observacion_caja),
  numero_colonia INTEGER NOT NULL CHECK (numero_colonia > 0),
  morfotipo TEXT NOT NULL,
  color TEXT,
  forma TEXT,
  borde TEXT,
  elevacion TEXT,
  tamano_mm NUMERIC(8,3),
  textura TEXT,
  pigmentacion TEXT,
  hemolisis TEXT,
  motivo_seleccion TEXT,
  posicion_en_placa TEXT,
  fecha_seleccion DATE NOT NULL,
  hora_seleccion TIME,
  responsable_seleccion TEXT,
  observaciones TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (id_caja_petri, numero_colonia)
);

CREATE TABLE subcultivos_petri (
  id_subcultivo UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  id_objeto UUID NOT NULL UNIQUE REFERENCES objetos_laboratorio(id_objeto),
  codigo_subcultivo TEXT NOT NULL UNIQUE,
  id_colonia UUID NOT NULL REFERENCES colonias_seleccionadas(id_colonia) ON DELETE RESTRICT,
  id_lote_medio UUID REFERENCES lotes_medio_cultivo(id_lote_medio),
  numero_subcultivo INTEGER NOT NULL CHECK (numero_subcultivo > 0),
  fecha_siembra DATE NOT NULL,
  hora_siembra TIME,
  responsable_siembra TEXT,
  metodo_siembra TEXT,
  temperatura_incubacion_c NUMERIC(5,2),
  tiempo_incubacion_horas NUMERIC(8,2),
  condicion_incubacion TEXT,
  estado_subcultivo TEXT NOT NULL DEFAULT 'creado',
  resultado_pureza TEXT,
  apto_para_extraccion BOOLEAN NOT NULL DEFAULT FALSE,
  trazabilidad JSONB,
  observaciones TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (id_colonia, numero_subcultivo)
);

CREATE TABLE observaciones_subcultivo (
  id_observacion_subcultivo UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  id_subcultivo UUID NOT NULL REFERENCES subcultivos_petri(id_subcultivo) ON DELETE CASCADE,
  fecha_observacion DATE NOT NULL,
  hora_observacion TIME,
  horas_incubacion NUMERIC(8,2),
  responsable_observacion TEXT,
  hay_crecimiento BOOLEAN,
  crecimiento_uniforme BOOLEAN,
  numero_morfotipos INTEGER CHECK (numero_morfotipos IS NULL OR numero_morfotipos >= 0),
  resultado_pureza TEXT,
  color_colonia TEXT,
  forma_colonia TEXT,
  borde_colonia TEXT,
  elevacion_colonia TEXT,
  cantidad_crecimiento TEXT,
  contaminacion_visible BOOLEAN,
  accion_recomendada TEXT,
  observaciones TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE aislamientos_bacterianos (
  id_aislamiento UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  id_objeto UUID NOT NULL UNIQUE REFERENCES objetos_laboratorio(id_objeto),
  codigo_aislamiento TEXT NOT NULL UNIQUE,
  id_subcultivo_origen UUID NOT NULL REFERENCES subcultivos_petri(id_subcultivo) ON DELETE RESTRICT,
  fecha_confirmacion DATE,
  responsable_confirmacion TEXT,
  estado_pureza TEXT,
  gram_resultado TEXT,
  oxidasa_resultado TEXT,
  catalasa_resultado TEXT,
  genero_identificado TEXT,
  especie_identificada TEXT,
  metodo_identificacion TEXT,
  confianza_identificacion NUMERIC(5,2),
  nivel_bioseguridad TEXT,
  estado_aislamiento TEXT NOT NULL DEFAULT 'activo',
  observaciones TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE stocks_bacterianos (
  id_stock_bacteriano UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  id_objeto UUID NOT NULL UNIQUE REFERENCES objetos_laboratorio(id_objeto),
  codigo_stock TEXT NOT NULL UNIQUE,
  id_aislamiento UUID NOT NULL REFERENCES aislamientos_bacterianos(id_aislamiento) ON DELETE RESTRICT,
  tipo_conservacion TEXT,
  crioprotector TEXT,
  concentracion_crioprotector_pct NUMERIC(6,2),
  volumen_ul NUMERIC(10,2),
  ubicacion_almacenamiento TEXT,
  temperatura_almacenamiento_c NUMERIC(6,2),
  fecha_conservacion DATE,
  estado_stock TEXT NOT NULL DEFAULT 'almacenado',
  observaciones TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE extracciones_adn (
  id_extraccion_adn UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  id_objeto UUID NOT NULL UNIQUE REFERENCES objetos_laboratorio(id_objeto),
  codigo_extraccion TEXT NOT NULL UNIQUE,
  id_subcultivo UUID NOT NULL REFERENCES subcultivos_petri(id_subcultivo) ON DELETE RESTRICT,
  id_aislamiento UUID REFERENCES aislamientos_bacterianos(id_aislamiento),
  fecha_extraccion DATE NOT NULL,
  hora_extraccion TIME,
  responsable_extraccion TEXT,
  metodo_extraccion TEXT,
  protocolo_extraccion TEXT,
  kit_reactivo TEXT,
  lote_reactivo TEXT,
  cantidad_biomasa TEXT,
  buffer_elucion TEXT,
  volumen_elucion_ul NUMERIC(10,2),
  resultado_extraccion TEXT,
  estado_extraccion TEXT NOT NULL DEFAULT 'pendiente_nanodrop',
  observaciones TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE viales_adn (
  id_vial_adn UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  id_objeto UUID NOT NULL UNIQUE REFERENCES objetos_laboratorio(id_objeto),
  codigo_vial TEXT NOT NULL UNIQUE,
  id_extraccion_adn UUID NOT NULL REFERENCES extracciones_adn(id_extraccion_adn) ON DELETE RESTRICT,
  numero_vial INTEGER NOT NULL CHECK (numero_vial > 0),
  tipo_material TEXT NOT NULL DEFAULT 'ADN bacteriano',
  volumen_inicial_ul NUMERIC(10,2),
  volumen_restante_ul NUMERIC(10,2),
  concentracion_ng_ul_estimada NUMERIC(12,4),
  estado_vial TEXT NOT NULL DEFAULT 'pendiente_nanodrop',
  ubicacion_almacenamiento TEXT,
  temperatura_almacenamiento_c NUMERIC(6,2),
  fecha_almacenamiento DATE,
  observaciones TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (id_extraccion_adn, numero_vial)
);

CREATE TABLE lecturas_nanodrop (
  id_lectura_nanodrop UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  codigo_lectura TEXT NOT NULL UNIQUE,
  id_vial_adn UUID NOT NULL REFERENCES viales_adn(id_vial_adn) ON DELETE RESTRICT,
  numero_lectura INTEGER NOT NULL CHECK (numero_lectura > 0),
  fecha_hora_lectura TIMESTAMPTZ NOT NULL,
  operador TEXT,
  equipo TEXT,
  tipo_molecula_esperada TEXT NOT NULL DEFAULT 'ADN',
  metodo_equipo TEXT,
  factor_conversion NUMERIC(10,4),
  ruta_optica_mm NUMERIC(8,4),
  abs_230 NUMERIC(12,6),
  abs_260 NUMERIC(12,6),
  abs_280 NUMERIC(12,6),
  ratio_260_280 NUMERIC(10,4),
  ratio_260_230 NUMERIC(10,4),
  concentracion_ng_ul NUMERIC(12,4),
  volumen_elucion_ul NUMERIC(10,2),
  rendimiento_total_ng NUMERIC(14,4),
  buffer_blanco TEXT,
  estado_calidad TEXT,
  accion_recomendada TEXT,
  observaciones TEXT,
  archivo_original_url TEXT,
  imagen_reporte_url TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (id_vial_adn, numero_lectura)
);

CREATE TABLE curva_nanodrop (
  id_punto_curva UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  id_lectura_nanodrop UUID NOT NULL REFERENCES lecturas_nanodrop(id_lectura_nanodrop) ON DELETE CASCADE,
  longitud_onda_nm NUMERIC(8,2) NOT NULL,
  absorbancia NUMERIC(12,6) NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (id_lectura_nanodrop, longitud_onda_nm)
);

CREATE TABLE pcr_ensayos (
  id_pcr_ensayo UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  codigo_ensayo TEXT NOT NULL UNIQUE,
  nombre_ensayo TEXT NOT NULL,
  gen_objetivo TEXT,
  primer_forward_nombre TEXT,
  primer_forward_secuencia TEXT,
  primer_reverse_nombre TEXT,
  primer_reverse_secuencia TEXT,
  tamano_amplicon_esperado_pb INTEGER,
  protocolo TEXT,
  temperatura_annealing_c NUMERIC(5,2),
  ciclos INTEGER,
  activo BOOLEAN NOT NULL DEFAULT TRUE,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE pcr_reacciones (
  id_pcr_reaccion UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  id_objeto UUID UNIQUE REFERENCES objetos_laboratorio(id_objeto),
  codigo_reaccion TEXT NOT NULL UNIQUE,
  id_vial_adn UUID NOT NULL REFERENCES viales_adn(id_vial_adn) ON DELETE RESTRICT,
  id_pcr_ensayo UUID NOT NULL REFERENCES pcr_ensayos(id_pcr_ensayo),
  fecha_pcr DATE NOT NULL,
  responsable_pcr TEXT,
  volumen_template_ul NUMERIC(10,2),
  concentracion_template_ng_ul NUMERIC(12,4),
  resultado_pcr TEXT,
  tamano_banda_observado_pb INTEGER,
  calidad_resultado TEXT,
  observaciones TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE geles_electroforesis (
  id_gel UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  id_objeto UUID NOT NULL UNIQUE REFERENCES objetos_laboratorio(id_objeto),
  codigo_gel TEXT NOT NULL UNIQUE,
  fecha_corrida DATE NOT NULL,
  responsable_corrida TEXT,
  concentracion_agarosa_pct NUMERIC(6,2),
  buffer_corrida TEXT,
  voltaje_v NUMERIC(8,2),
  tiempo_corrida_min NUMERIC(8,2),
  marcador_peso_molecular TEXT,
  tincion TEXT,
  estado_gel TEXT NOT NULL DEFAULT 'registrado',
  observaciones TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE carriles_gel (
  id_carril_gel UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  id_gel UUID NOT NULL REFERENCES geles_electroforesis(id_gel) ON DELETE CASCADE,
  numero_carril INTEGER NOT NULL CHECK (numero_carril > 0),
  id_pcr_reaccion UUID REFERENCES pcr_reacciones(id_pcr_reaccion),
  tipo_carril TEXT NOT NULL DEFAULT 'muestra',
  codigo_muestra_visible TEXT,
  banda_detectada BOOLEAN,
  tamano_estimado_pb INTEGER,
  intensidad_relativa NUMERIC(6,3),
  calidad_carril TEXT,
  observaciones TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (id_gel, numero_carril)
);

CREATE TABLE secuenciaciones (
  id_secuenciacion UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  id_objeto UUID UNIQUE REFERENCES objetos_laboratorio(id_objeto),
  codigo_secuenciacion TEXT NOT NULL UNIQUE,
  id_pcr_reaccion UUID NOT NULL REFERENCES pcr_reacciones(id_pcr_reaccion) ON DELETE RESTRICT,
  proveedor TEXT,
  metodo_secuenciacion TEXT,
  primer_usado TEXT,
  fecha_envio DATE,
  fecha_recepcion_resultado DATE,
  archivo_ab1_url TEXT,
  archivo_fasta_url TEXT,
  secuencia_consenso TEXT,
  longitud_consenso_pb INTEGER,
  calidad_promedio NUMERIC(6,3),
  estado_secuenciacion TEXT NOT NULL DEFAULT 'pendiente',
  observaciones TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE resultados_blast (
  id_resultado_blast UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  id_secuenciacion UUID NOT NULL REFERENCES secuenciaciones(id_secuenciacion) ON DELETE CASCADE,
  corrida_blast TEXT,
  base_datos TEXT,
  fecha_corrida TIMESTAMPTZ,
  ranking INTEGER NOT NULL CHECK (ranking > 0),
  accession TEXT,
  taxon_id TEXT,
  organismo TEXT,
  porcentaje_identidad NUMERIC(8,4),
  query_cover NUMERIC(8,4),
  e_value NUMERIC(20,10),
  bit_score NUMERIC(14,4),
  longitud_alineamiento INTEGER,
  interpretacion TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (id_secuenciacion, ranking)
);

CREATE TABLE media_archivos (
  id_media UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  storage_uri TEXT NOT NULL,
  file_name TEXT,
  mime_type TEXT,
  media_type TEXT NOT NULL DEFAULT 'imagen',
  width_px INTEGER,
  height_px INTEGER,
  size_bytes BIGINT,
  sha256 TEXT,
  captured_at TIMESTAMPTZ,
  uploaded_by TEXT,
  metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE media_vinculos (
  id_media_vinculo UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  id_media UUID NOT NULL REFERENCES media_archivos(id_media) ON DELETE CASCADE,
  id_objeto UUID NOT NULL REFERENCES objetos_laboratorio(id_objeto) ON DELETE CASCADE,
  rol TEXT NOT NULL DEFAULT 'evidencia',
  descripcion TEXT,
  es_principal BOOLEAN NOT NULL DEFAULT FALSE,
  id_observacion_caja UUID REFERENCES observaciones_caja_petri(id_observacion_caja),
  id_observacion_subcultivo UUID REFERENCES observaciones_subcultivo(id_observacion_subcultivo),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (id_media, id_objeto, rol)
);

CREATE TABLE lotes_impresion_etiquetas (
  id_lote_impresion UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  codigo_lote_impresion TEXT NOT NULL UNIQUE,
  formato_papel TEXT NOT NULL DEFAULT 'A4',
  columnas INTEGER NOT NULL DEFAULT 2,
  creado_por TEXT,
  creado_en TIMESTAMPTZ NOT NULL DEFAULT now(),
  estado_impresion TEXT NOT NULL DEFAULT 'preparado',
  observaciones TEXT
);

CREATE TABLE etiquetas_fisicas (
  id_etiqueta UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  id_objeto UUID NOT NULL REFERENCES objetos_laboratorio(id_objeto) ON DELETE CASCADE,
  codigo_visible TEXT NOT NULL,
  valor_qr TEXT NOT NULL,
  valor_barcode TEXT NOT NULL,
  formato TEXT NOT NULL DEFAULT 'qr_barcode',
  plantilla TEXT NOT NULL DEFAULT 'lab_object_v1',
  version_plantilla TEXT NOT NULL DEFAULT '1.0',
  estado_etiqueta TEXT NOT NULL DEFAULT 'generada',
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (id_objeto, codigo_visible, plantilla)
);

CREATE TABLE items_lote_impresion (
  id_item_lote_impresion UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  id_lote_impresion UUID NOT NULL REFERENCES lotes_impresion_etiquetas(id_lote_impresion) ON DELETE CASCADE,
  id_etiqueta UUID NOT NULL REFERENCES etiquetas_fisicas(id_etiqueta) ON DELETE RESTRICT,
  posicion_fila INTEGER,
  posicion_columna INTEGER,
  estado_item TEXT NOT NULL DEFAULT 'pendiente',
  UNIQUE (id_lote_impresion, id_etiqueta)
);

CREATE TABLE plantillas_registro (
  id_plantilla UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tipo_objeto TEXT NOT NULL,
  nombre TEXT NOT NULL,
  descripcion TEXT,
  version TEXT NOT NULL DEFAULT '1.0',
  activa BOOLEAN NOT NULL DEFAULT TRUE,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (tipo_objeto, nombre, version)
);

CREATE TABLE campos_plantilla (
  id_campo UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  id_plantilla UUID NOT NULL REFERENCES plantillas_registro(id_plantilla) ON DELETE CASCADE,
  clave TEXT NOT NULL,
  etiqueta TEXT NOT NULL,
  tipo_dato TEXT NOT NULL CHECK (tipo_dato IN ('texto', 'numero', 'booleano', 'fecha', 'fecha_hora', 'json')),
  unidad TEXT,
  obligatorio BOOLEAN NOT NULL DEFAULT FALSE,
  opciones JSONB,
  candidato_feature_ia BOOLEAN NOT NULL DEFAULT FALSE,
  orden INTEGER NOT NULL DEFAULT 0,
  activo BOOLEAN NOT NULL DEFAULT TRUE,
  UNIQUE (id_plantilla, clave)
);

CREATE TABLE valores_campo_personalizado (
  id_valor UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  id_campo UUID NOT NULL REFERENCES campos_plantilla(id_campo) ON DELETE CASCADE,
  id_objeto UUID NOT NULL REFERENCES objetos_laboratorio(id_objeto) ON DELETE CASCADE,
  valor_texto TEXT,
  valor_numero NUMERIC,
  valor_booleano BOOLEAN,
  valor_fecha DATE,
  valor_fecha_hora TIMESTAMPTZ,
  valor_json JSONB,
  fuente TEXT,
  capturado_por TEXT,
  captured_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (id_campo, id_objeto)
);

CREATE TABLE corridas_analisis (
  id_corrida UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tipo_analisis TEXT NOT NULL,
  nombre_modelo TEXT,
  version_modelo TEXT,
  parametros JSONB NOT NULL DEFAULT '{}'::jsonb,
  estado_corrida TEXT NOT NULL DEFAULT 'registrada',
  iniciada_en TIMESTAMPTZ,
  finalizada_en TIMESTAMPTZ,
  ejecutada_por TEXT,
  notas TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE anotaciones_media (
  id_anotacion UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  id_media UUID NOT NULL REFERENCES media_archivos(id_media) ON DELETE CASCADE,
  id_objeto UUID REFERENCES objetos_laboratorio(id_objeto) ON DELETE CASCADE,
  etiqueta TEXT NOT NULL,
  geometria JSONB,
  confianza NUMERIC(6,4),
  anotado_por TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE resultados_analisis_objeto (
  id_resultado_analisis UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  id_corrida UUID NOT NULL REFERENCES corridas_analisis(id_corrida) ON DELETE CASCADE,
  id_objeto UUID NOT NULL REFERENCES objetos_laboratorio(id_objeto) ON DELETE CASCADE,
  id_media UUID REFERENCES media_archivos(id_media) ON DELETE SET NULL,
  mediciones JSONB NOT NULL DEFAULT '{}'::jsonb,
  predicciones JSONB NOT NULL DEFAULT '{}'::jsonb,
  calidad_resultado NUMERIC(6,4),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE dataset_snapshots (
  id_dataset_snapshot UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  nombre TEXT NOT NULL,
  descripcion TEXT,
  consulta_origen TEXT,
  filtros JSONB NOT NULL DEFAULT '{}'::jsonb,
  definicion_features JSONB NOT NULL DEFAULT '{}'::jsonb,
  filas_estimadas INTEGER,
  creado_para TEXT,
  creado_por TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE eventos_auditoria (
  id_evento UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tipo_evento TEXT NOT NULL,
  id_objeto UUID REFERENCES objetos_laboratorio(id_objeto) ON DELETE SET NULL,
  entidad_tabla TEXT,
  entidad_id UUID,
  actor TEXT,
  id_actor UUID REFERENCES usuarios_laboratorio(id_usuario) ON DELETE SET NULL,
  id_sesion UUID REFERENCES sesiones_usuario(id_sesion) ON DELETE SET NULL,
  accion TEXT,
  recurso TEXT,
  permiso TEXT,
  metodo_http TEXT,
  ruta TEXT,
  estado_http INTEGER,
  exito BOOLEAN NOT NULL DEFAULT TRUE,
  ip TEXT,
  user_agent TEXT,
  correlation_id UUID NOT NULL DEFAULT gen_random_uuid(),
  before_data JSONB,
  after_data JSONB,
  reversible BOOLEAN NOT NULL DEFAULT FALSE,
  detalles JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_peces_recepcion ON peces(id_recepcion);
CREATE INDEX idx_muestras_pez ON muestras_biologicas(id_pez);
CREATE INDEX idx_cajas_muestra ON cajas_petri(id_muestra_biologica);
CREATE INDEX idx_obs_caja ON observaciones_caja_petri(id_caja_petri);
CREATE INDEX idx_colonias_caja ON colonias_seleccionadas(id_caja_petri);
CREATE INDEX idx_subcultivos_colonia ON subcultivos_petri(id_colonia);
CREATE INDEX idx_extracciones_subcultivo ON extracciones_adn(id_subcultivo);
CREATE INDEX idx_viales_extraccion ON viales_adn(id_extraccion_adn);
CREATE INDEX idx_nanodrop_vial ON lecturas_nanodrop(id_vial_adn);
CREATE INDEX idx_media_objeto ON media_vinculos(id_objeto);
CREATE INDEX idx_labels_objeto ON etiquetas_fisicas(id_objeto);
CREATE INDEX idx_valores_objeto ON valores_campo_personalizado(id_objeto);
CREATE INDEX idx_audit_objeto ON eventos_auditoria(id_objeto);
CREATE INDEX idx_audit_actor_fecha ON eventos_auditoria(id_actor, created_at DESC);
CREATE INDEX idx_audit_accion_fecha ON eventos_auditoria(accion, created_at DESC);
CREATE INDEX idx_sesiones_usuario_activas ON sesiones_usuario(id_usuario, expira_at)
  WHERE revocado_at IS NULL;
CREATE INDEX idx_grupos_miembros_usuario ON grupos_miembros(id_usuario);
CREATE INDEX idx_permisos_modulo ON permisos_acceso(modulo, recurso, accion);

-- La auditoría conserva el identificador histórico aunque el objeto experimental
-- ya no exista; por eso id_objeto no tiene una FK que provoque su truncado en cascada.
ALTER TABLE eventos_auditoria
  DROP CONSTRAINT IF EXISTS eventos_auditoria_id_objeto_fkey;

CREATE OR REPLACE FUNCTION proteger_ultimo_superadmin()
RETURNS TRIGGER AS $$
DECLARE
  pierde_nivel BOOLEAN;
  restantes INTEGER;
BEGIN
  PERFORM pg_advisory_xact_lock(hashtextextended('fagolab:superadmin', 0));
  IF TG_OP = 'DELETE' THEN
    pierde_nivel := OLD.es_superadmin AND OLD.activo AND OLD.estado_cuenta='activa';
  ELSE
    pierde_nivel :=
      OLD.es_superadmin AND OLD.activo AND OLD.estado_cuenta='activa'
      AND NOT (NEW.es_superadmin AND NEW.activo AND NEW.estado_cuenta='activa');
  END IF;
  IF pierde_nivel THEN
    SELECT count(*)::int INTO restantes
    FROM usuarios_laboratorio
    WHERE es_superadmin AND activo AND estado_cuenta='activa'
      AND id_usuario<>OLD.id_usuario;
    IF restantes < 1 THEN
      RAISE EXCEPTION 'Debe permanecer al menos una superadministradora activa.'
        USING ERRCODE = '23514';
    END IF;
  END IF;
  IF TG_OP = 'DELETE' THEN RETURN OLD; END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION proteger_truncado_superadmins()
RETURNS TRIGGER AS $$
BEGIN
  IF EXISTS (
    SELECT 1 FROM usuarios_laboratorio
    WHERE es_superadmin AND activo AND estado_cuenta='activa'
  ) THEN
    RAISE EXCEPTION 'No se puede vaciar la tabla de usuarios: debe permanecer una superadministradora.'
      USING ERRCODE = '23514';
  END IF;
  RETURN NULL;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_proyectos_updated_at BEFORE UPDATE ON proyectos
FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE TRIGGER trg_usuarios_updated_at BEFORE UPDATE ON usuarios_laboratorio
FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE TRIGGER trg_proteger_superadmin_update
BEFORE UPDATE OF es_superadmin, activo, estado_cuenta ON usuarios_laboratorio
FOR EACH ROW EXECUTE FUNCTION proteger_ultimo_superadmin();

CREATE TRIGGER trg_proteger_superadmin_delete
BEFORE DELETE ON usuarios_laboratorio
FOR EACH ROW EXECUTE FUNCTION proteger_ultimo_superadmin();

CREATE TRIGGER trg_proteger_superadmin_truncate
BEFORE TRUNCATE ON usuarios_laboratorio
FOR EACH STATEMENT EXECUTE FUNCTION proteger_truncado_superadmins();

ALTER TABLE usuarios_laboratorio ENABLE ALWAYS TRIGGER trg_proteger_superadmin_update;
ALTER TABLE usuarios_laboratorio ENABLE ALWAYS TRIGGER trg_proteger_superadmin_delete;
ALTER TABLE usuarios_laboratorio ENABLE ALWAYS TRIGGER trg_proteger_superadmin_truncate;

CREATE TRIGGER trg_roles_updated_at BEFORE UPDATE ON roles_acceso
FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE TRIGGER trg_permisos_updated_at BEFORE UPDATE ON permisos_acceso
FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE TRIGGER trg_grupos_updated_at BEFORE UPDATE ON grupos_acceso
FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE TRIGGER trg_objetos_updated_at BEFORE UPDATE ON objetos_laboratorio
FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE TRIGGER trg_recepciones_updated_at BEFORE UPDATE ON recepciones_lote
FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE TRIGGER trg_peces_updated_at BEFORE UPDATE ON peces
FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE TRIGGER trg_muestras_updated_at BEFORE UPDATE ON muestras_biologicas
FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE TRIGGER trg_cajas_updated_at BEFORE UPDATE ON cajas_petri
FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE TRIGGER trg_colonias_updated_at BEFORE UPDATE ON colonias_seleccionadas
FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE TRIGGER trg_subcultivos_updated_at BEFORE UPDATE ON subcultivos_petri
FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE TRIGGER trg_aislamientos_updated_at BEFORE UPDATE ON aislamientos_bacterianos
FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE TRIGGER trg_stocks_updated_at BEFORE UPDATE ON stocks_bacterianos
FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE TRIGGER trg_extracciones_updated_at BEFORE UPDATE ON extracciones_adn
FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE TRIGGER trg_viales_updated_at BEFORE UPDATE ON viales_adn
FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE TRIGGER trg_nanodrop_updated_at BEFORE UPDATE ON lecturas_nanodrop
FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE TRIGGER trg_pcr_ensayos_updated_at BEFORE UPDATE ON pcr_ensayos
FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE TRIGGER trg_pcr_reacciones_updated_at BEFORE UPDATE ON pcr_reacciones
FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE TRIGGER trg_geles_updated_at BEFORE UPDATE ON geles_electroforesis
FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE TRIGGER trg_secuenciaciones_updated_at BEFORE UPDATE ON secuenciaciones
FOR EACH ROW EXECUTE FUNCTION set_updated_at();


-- Contador requerido por la semilla de tareas; las migraciones anteriores lo
-- enriquecen con los contadores del flujo experimental.
CREATE TABLE IF NOT EXISTS contadores (
  entidad TEXT PRIMARY KEY,
  valor INTEGER NOT NULL DEFAULT 0
);

-- 017-019: espejo del esquema final de colaboración. Las migraciones homónimas lo aplican a BDs existentes.
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

-- 018_tareas.sql -- flujo configurable y trabajo diario. id_objeto/id_media no llevan FK deliberadamente.
CREATE TABLE IF NOT EXISTS tareas_flujos (id_flujo UUID PRIMARY KEY DEFAULT gen_random_uuid(), clave TEXT NOT NULL UNIQUE, nombre TEXT NOT NULL, descripcion TEXT, activo BOOLEAN NOT NULL DEFAULT TRUE, created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now());
CREATE TABLE IF NOT EXISTS tareas_estados (id_estado UUID PRIMARY KEY DEFAULT gen_random_uuid(), id_flujo UUID NOT NULL REFERENCES tareas_flujos(id_flujo) ON DELETE CASCADE, clave TEXT NOT NULL, nombre TEXT NOT NULL, categoria TEXT NOT NULL CHECK (categoria IN ('por_hacer','en_progreso','hecho')), color TEXT, orden INTEGER NOT NULL DEFAULT 0, es_inicial BOOLEAN NOT NULL DEFAULT FALSE, pos_x NUMERIC, pos_y NUMERIC, UNIQUE(id_flujo, clave));
CREATE UNIQUE INDEX IF NOT EXISTS uq_tareas_estado_inicial ON tareas_estados(id_flujo) WHERE es_inicial;
CREATE TABLE IF NOT EXISTS tareas_transiciones (id_transicion UUID PRIMARY KEY DEFAULT gen_random_uuid(), id_flujo UUID NOT NULL REFERENCES tareas_flujos(id_flujo) ON DELETE CASCADE, id_estado_origen UUID REFERENCES tareas_estados(id_estado) ON DELETE CASCADE, id_estado_destino UUID NOT NULL REFERENCES tareas_estados(id_estado) ON DELETE CASCADE, clave TEXT NOT NULL, nombre TEXT NOT NULL, orden INTEGER NOT NULL DEFAULT 0, activo BOOLEAN NOT NULL DEFAULT TRUE, UNIQUE(id_flujo, clave));
CREATE TABLE IF NOT EXISTS tareas_reglas_transicion (id_regla UUID PRIMARY KEY DEFAULT gen_random_uuid(), id_transicion UUID NOT NULL REFERENCES tareas_transiciones(id_transicion) ON DELETE CASCADE, fase TEXT NOT NULL CHECK (fase IN ('condicion','validador','post_funcion')), tipo TEXT NOT NULL, configuracion JSONB NOT NULL DEFAULT '{}'::jsonb, mensaje_error TEXT, orden INTEGER NOT NULL DEFAULT 0, activo BOOLEAN NOT NULL DEFAULT TRUE);
CREATE UNIQUE INDEX IF NOT EXISTS uq_tareas_regla_transicion ON tareas_reglas_transicion(id_transicion, fase, tipo, orden);

CREATE TABLE IF NOT EXISTS tareas_tipos (id_tipo UUID PRIMARY KEY DEFAULT gen_random_uuid(), clave TEXT NOT NULL UNIQUE, nombre TEXT NOT NULL, descripcion TEXT, icono TEXT, color TEXT, jerarquia TEXT NOT NULL DEFAULT 'tarea' CHECK (jerarquia IN ('epica','tarea','subtarea')), id_flujo UUID REFERENCES tareas_flujos(id_flujo) ON DELETE SET NULL, orden INTEGER NOT NULL DEFAULT 0, activo BOOLEAN NOT NULL DEFAULT TRUE, created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now());
CREATE TABLE IF NOT EXISTS tareas_espacios (id_espacio UUID PRIMARY KEY DEFAULT gen_random_uuid(), clave TEXT NOT NULL UNIQUE, nombre TEXT NOT NULL, descripcion TEXT, id_flujo UUID REFERENCES tareas_flujos(id_flujo) ON DELETE SET NULL, id_lider UUID REFERENCES usuarios_laboratorio(id_usuario) ON DELETE SET NULL, activo BOOLEAN NOT NULL DEFAULT TRUE, created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now());
CREATE TABLE IF NOT EXISTS tareas (id_tarea UUID PRIMARY KEY DEFAULT gen_random_uuid(), id_espacio UUID NOT NULL REFERENCES tareas_espacios(id_espacio) ON DELETE CASCADE, clave TEXT NOT NULL UNIQUE, titulo TEXT NOT NULL, descripcion TEXT, tipo TEXT NOT NULL DEFAULT 'tarea', prioridad TEXT NOT NULL DEFAULT 'media' CHECK (prioridad IN ('baja','media','alta','critica')), id_estado UUID NOT NULL REFERENCES tareas_estados(id_estado), id_asignado UUID REFERENCES usuarios_laboratorio(id_usuario) ON DELETE SET NULL, id_reportador UUID REFERENCES usuarios_laboratorio(id_usuario) ON DELETE SET NULL, id_padre UUID REFERENCES tareas(id_tarea) ON DELETE SET NULL, fecha_inicio DATE, fecha_limite DATE, completada_at TIMESTAMPTZ, etiquetas TEXT[] NOT NULL DEFAULT '{}', orden_tablero NUMERIC NOT NULL DEFAULT 0, id_objeto UUID, codigo_objeto TEXT, id_tipo UUID REFERENCES tareas_tipos(id_tipo) ON DELETE SET NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now());
CREATE INDEX IF NOT EXISTS idx_tareas_espacio_estado ON tareas(id_espacio,id_estado,orden_tablero); CREATE INDEX IF NOT EXISTS idx_tareas_asignado ON tareas(id_asignado) WHERE completada_at IS NULL;
CREATE TABLE IF NOT EXISTS tareas_observadores (id_tarea UUID NOT NULL REFERENCES tareas(id_tarea) ON DELETE CASCADE,id_usuario UUID NOT NULL REFERENCES usuarios_laboratorio(id_usuario) ON DELETE CASCADE,created_at TIMESTAMPTZ NOT NULL DEFAULT now(),PRIMARY KEY(id_tarea,id_usuario));
CREATE TABLE IF NOT EXISTS tareas_comentarios (id_comentario UUID PRIMARY KEY DEFAULT gen_random_uuid(),id_tarea UUID NOT NULL REFERENCES tareas(id_tarea) ON DELETE CASCADE,id_autor UUID REFERENCES usuarios_laboratorio(id_usuario) ON DELETE SET NULL,cuerpo TEXT NOT NULL,editado_at TIMESTAMPTZ,eliminado_at TIMESTAMPTZ,created_at TIMESTAMPTZ NOT NULL DEFAULT now(),updated_at TIMESTAMPTZ NOT NULL DEFAULT now());
CREATE TABLE IF NOT EXISTS tareas_adjuntos (id_adjunto UUID PRIMARY KEY DEFAULT gen_random_uuid(),id_tarea UUID NOT NULL REFERENCES tareas(id_tarea) ON DELETE CASCADE,id_media UUID NOT NULL,nombre TEXT,metadata JSONB NOT NULL DEFAULT '{}'::jsonb,subido_por UUID REFERENCES usuarios_laboratorio(id_usuario) ON DELETE SET NULL,created_at TIMESTAMPTZ NOT NULL DEFAULT now());
CREATE TABLE IF NOT EXISTS tareas_actividad (id_actividad UUID PRIMARY KEY DEFAULT gen_random_uuid(),id_tarea UUID NOT NULL REFERENCES tareas(id_tarea) ON DELETE CASCADE,id_actor UUID REFERENCES usuarios_laboratorio(id_usuario) ON DELETE SET NULL,tipo TEXT NOT NULL,campo TEXT,antes JSONB,despues JSONB,metadata JSONB NOT NULL DEFAULT '{}'::jsonb,created_at TIMESTAMPTZ NOT NULL DEFAULT now());
CREATE INDEX IF NOT EXISTS idx_tareas_actividad_tarea ON tareas_actividad(id_tarea,created_at DESC);
CREATE TABLE IF NOT EXISTS tareas_campos (id_campo UUID PRIMARY KEY DEFAULT gen_random_uuid(),id_espacio UUID NOT NULL REFERENCES tareas_espacios(id_espacio) ON DELETE CASCADE,clave TEXT NOT NULL,nombre TEXT NOT NULL,tipo TEXT NOT NULL,configuracion JSONB NOT NULL DEFAULT '{}'::jsonb,requerido BOOLEAN NOT NULL DEFAULT FALSE,orden INTEGER NOT NULL DEFAULT 0,activo BOOLEAN NOT NULL DEFAULT TRUE,UNIQUE(id_espacio,clave));
CREATE TABLE IF NOT EXISTS tareas_valores_campo (id_tarea UUID NOT NULL REFERENCES tareas(id_tarea) ON DELETE CASCADE,id_campo UUID NOT NULL REFERENCES tareas_campos(id_campo) ON DELETE CASCADE,valor JSONB,updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),PRIMARY KEY(id_tarea,id_campo));
CREATE TABLE IF NOT EXISTS tareas_esquemas_permisos (id_esquema UUID PRIMARY KEY DEFAULT gen_random_uuid(),id_espacio UUID NOT NULL UNIQUE REFERENCES tareas_espacios(id_espacio) ON DELETE CASCADE,nombre TEXT NOT NULL,activo BOOLEAN NOT NULL DEFAULT TRUE,created_at TIMESTAMPTZ NOT NULL DEFAULT now(),updated_at TIMESTAMPTZ NOT NULL DEFAULT now());
CREATE TABLE IF NOT EXISTS tareas_esquema_reglas (id_regla UUID PRIMARY KEY DEFAULT gen_random_uuid(),id_esquema UUID NOT NULL REFERENCES tareas_esquemas_permisos(id_esquema) ON DELETE CASCADE,accion TEXT NOT NULL CHECK (accion IN ('ver','crear','editar','transicionar','asignar','comentar','adjuntar','eliminar','gestionar')),tipo_sujeto TEXT NOT NULL CHECK (tipo_sujeto IN ('todos','rol','grupo','usuario','asignado','reportador','observador','lider_espacio')),id_sujeto UUID,permitir BOOLEAN NOT NULL DEFAULT TRUE,orden INTEGER NOT NULL DEFAULT 0);

INSERT INTO tareas_flujos(clave,nombre,descripcion) VALUES('basico','Flujo bÃ¡sico','Pendiente â†’ En progreso â†’ En revisiÃ³n â†’ Hecho') ON CONFLICT(clave) DO UPDATE SET nombre=EXCLUDED.nombre,descripcion=EXCLUDED.descripcion;
WITH f AS (SELECT id_flujo FROM tareas_flujos WHERE clave='basico') INSERT INTO tareas_estados(id_flujo,clave,nombre,categoria,orden,es_inicial) SELECT id_flujo,v.clave,v.nombre,v.categoria,v.orden,v.inicial FROM f CROSS JOIN (VALUES ('pendiente','Pendiente','por_hacer',0,TRUE),('en_progreso','En progreso','en_progreso',1,FALSE),('en_revision','En revisiÃ³n','en_progreso',2,FALSE),('hecho','Hecho','hecho',3,FALSE)) AS v(clave,nombre,categoria,orden,inicial) ON CONFLICT(id_flujo,clave) DO UPDATE SET nombre=EXCLUDED.nombre,categoria=EXCLUDED.categoria,orden=EXCLUDED.orden,es_inicial=EXCLUDED.es_inicial;
WITH f AS (SELECT id_flujo FROM tareas_flujos WHERE clave='basico'), s AS (SELECT id_estado,clave FROM tareas_estados WHERE id_flujo=(SELECT id_flujo FROM f)) INSERT INTO tareas_transiciones(id_flujo,id_estado_origen,id_estado_destino,clave,nombre) SELECT f.id_flujo,o.id_estado,d.id_estado,v.clave,v.nombre FROM f CROSS JOIN (VALUES ('pendiente','en_progreso','iniciar','Iniciar'),('en_progreso','en_revision','enviar_revision','Enviar a revisiÃ³n'),('en_revision','hecho','aprobar','Aprobar'),('en_revision','en_progreso','devolver','Devolver'),('hecho','pendiente','reabrir','Reabrir')) v(origen,destino,clave,nombre) JOIN s o ON o.clave=v.origen JOIN s d ON d.clave=v.destino ON CONFLICT(id_flujo,clave) DO UPDATE SET id_estado_origen=EXCLUDED.id_estado_origen,id_estado_destino=EXCLUDED.id_estado_destino,nombre=EXCLUDED.nombre;
WITH t AS (SELECT id_transicion,clave FROM tareas_transiciones WHERE id_flujo=(SELECT id_flujo FROM tareas_flujos WHERE clave='basico')) INSERT INTO tareas_reglas_transicion(id_transicion,fase,tipo,configuracion,mensaje_error,orden) SELECT id_transicion,'condicion','solo_asignado','{}'::jsonb,'Solo la persona asignada puede enviar a revisiÃ³n.',0 FROM t WHERE clave='enviar_revision' UNION ALL SELECT id_transicion,'validador','campo_requerido','{"campo":"descripcion"}'::jsonb,'Se requiere una descripciÃ³n antes de aprobar.',0 FROM t WHERE clave='aprobar' UNION ALL SELECT id_transicion,'post_funcion','registrar_actividad','{}'::jsonb,NULL,1 FROM t UNION ALL SELECT id_transicion,'post_funcion','notificar_observadores','{}'::jsonb,NULL,2 FROM t WHERE clave='aprobar' ON CONFLICT (id_transicion,fase,tipo,orden) DO UPDATE SET configuracion=EXCLUDED.configuracion,mensaje_error=EXCLUDED.mensaje_error;
INSERT INTO tareas_espacios(clave,nombre,descripcion,id_flujo) VALUES('TAR','Laboratorio','Trabajo colaborativo del laboratorio',(SELECT id_flujo FROM tareas_flujos WHERE clave='basico')) ON CONFLICT(clave) DO UPDATE SET nombre=EXCLUDED.nombre,descripcion=EXCLUDED.descripcion,id_flujo=EXCLUDED.id_flujo;
INSERT INTO tareas_esquemas_permisos(id_espacio,nombre) VALUES((SELECT id_espacio FROM tareas_espacios WHERE clave='TAR'),'EstÃ¡ndar') ON CONFLICT(id_espacio) DO UPDATE SET nombre=EXCLUDED.nombre;
INSERT INTO contadores(entidad,valor) VALUES('tarea:TAR',0) ON CONFLICT(entidad) DO NOTHING;
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname='trg_tareas_flujos_updated_at') THEN CREATE TRIGGER trg_tareas_flujos_updated_at BEFORE UPDATE ON tareas_flujos FOR EACH ROW EXECUTE FUNCTION set_updated_at(); END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname='trg_tareas_updated_at') THEN CREATE TRIGGER trg_tareas_updated_at BEFORE UPDATE ON tareas FOR EACH ROW EXECUTE FUNCTION set_updated_at(); END IF;
END $$;

-- 019_ia_agente.sql -- configuraciÃ³n cifrada, conversaciones y auditorÃ­a del agente.
CREATE TABLE IF NOT EXISTS ia_configuracion (id_configuracion UUID PRIMARY KEY DEFAULT gen_random_uuid(),clave TEXT NOT NULL UNIQUE DEFAULT 'global',proveedor TEXT NOT NULL DEFAULT 'deepseek',base_url TEXT NOT NULL DEFAULT 'https://api.deepseek.com',modelo TEXT NOT NULL DEFAULT 'deepseek-chat',api_key_cifrada BYTEA,api_key_pista TEXT,habilitado BOOLEAN NOT NULL DEFAULT FALSE,max_iteraciones INTEGER NOT NULL DEFAULT 8 CHECK(max_iteraciones BETWEEN 1 AND 50),temperatura NUMERIC NOT NULL DEFAULT 0.2 CHECK(temperatura BETWEEN 0 AND 2),precios JSONB NOT NULL DEFAULT '{}'::jsonb,verificado_at TIMESTAMPTZ,verificado_ok BOOLEAN,verificado_detalle TEXT,updated_by UUID REFERENCES usuarios_laboratorio(id_usuario) ON DELETE SET NULL,created_at TIMESTAMPTZ NOT NULL DEFAULT now(),updated_at TIMESTAMPTZ NOT NULL DEFAULT now());
CREATE TABLE IF NOT EXISTS ia_politicas (id_politica UUID PRIMARY KEY DEFAULT gen_random_uuid(),clave TEXT NOT NULL UNIQUE,nombre TEXT NOT NULL,prompt_sistema TEXT NOT NULL,reglas JSONB NOT NULL DEFAULT '[]'::jsonb,dominios_permitidos TEXT[] NOT NULL DEFAULT '{}',dominios_bloqueados TEXT[] NOT NULL DEFAULT '{}',herramientas_habilitadas TEXT[] NOT NULL DEFAULT '{}',comandos_bloqueados TEXT[] NOT NULL DEFAULT '{}',max_iteraciones INTEGER NOT NULL DEFAULT 8,activa BOOLEAN NOT NULL DEFAULT TRUE,created_at TIMESTAMPTZ NOT NULL DEFAULT now(),updated_at TIMESTAMPTZ NOT NULL DEFAULT now());
CREATE TABLE IF NOT EXISTS ia_conectores (id_conector UUID PRIMARY KEY DEFAULT gen_random_uuid(),clave TEXT NOT NULL UNIQUE,nombre TEXT NOT NULL,tipo TEXT NOT NULL CHECK(tipo IN ('busqueda_web','mcp','http','doi')),base_url TEXT NOT NULL,api_key_cifrada BYTEA,configuracion JSONB NOT NULL DEFAULT '{}'::jsonb,habilitado BOOLEAN NOT NULL DEFAULT FALSE,created_at TIMESTAMPTZ NOT NULL DEFAULT now(),updated_at TIMESTAMPTZ NOT NULL DEFAULT now());
CREATE TABLE IF NOT EXISTS ia_conversaciones (id_conversacion UUID PRIMARY KEY DEFAULT gen_random_uuid(),id_usuario UUID NOT NULL REFERENCES usuarios_laboratorio(id_usuario) ON DELETE CASCADE,id_politica UUID REFERENCES ia_politicas(id_politica) ON DELETE SET NULL,titulo TEXT,modo TEXT NOT NULL DEFAULT 'ask' CHECK(modo IN ('ask','agente','super')),shell_habilitado BOOLEAN NOT NULL DEFAULT FALSE,tokens_entrada BIGINT NOT NULL DEFAULT 0,tokens_salida BIGINT NOT NULL DEFAULT 0,costo_acumulado NUMERIC NOT NULL DEFAULT 0,created_at TIMESTAMPTZ NOT NULL DEFAULT now(),updated_at TIMESTAMPTZ NOT NULL DEFAULT now());
CREATE TABLE IF NOT EXISTS ia_mensajes (id_mensaje UUID PRIMARY KEY DEFAULT gen_random_uuid(),id_conversacion UUID NOT NULL REFERENCES ia_conversaciones(id_conversacion) ON DELETE CASCADE,rol TEXT NOT NULL CHECK(rol IN ('system','user','assistant','tool')),contenido TEXT,tool_calls JSONB NOT NULL DEFAULT '[]'::jsonb,metadata JSONB NOT NULL DEFAULT '{}'::jsonb,tokens_entrada INTEGER,tokens_salida INTEGER,costo NUMERIC,created_at TIMESTAMPTZ NOT NULL DEFAULT now());
CREATE TABLE IF NOT EXISTS ia_ejecuciones (id_ejecucion UUID PRIMARY KEY DEFAULT gen_random_uuid(),id_conversacion UUID NOT NULL REFERENCES ia_conversaciones(id_conversacion) ON DELETE CASCADE,id_usuario UUID NOT NULL REFERENCES usuarios_laboratorio(id_usuario) ON DELETE CASCADE,estado TEXT NOT NULL DEFAULT 'pendiente' CHECK(estado IN ('pendiente','ejecutando','completada','error','cancelada')),modo TEXT NOT NULL CHECK(modo IN ('ask','agente','super')),error TEXT,iniciada_at TIMESTAMPTZ,finalizada_at TIMESTAMPTZ,created_at TIMESTAMPTZ NOT NULL DEFAULT now());
CREATE TABLE IF NOT EXISTS ia_llamadas_herramienta (id_llamada UUID PRIMARY KEY DEFAULT gen_random_uuid(),id_ejecucion UUID NOT NULL REFERENCES ia_ejecuciones(id_ejecucion) ON DELETE CASCADE,nombre TEXT NOT NULL,argumentos JSONB NOT NULL DEFAULT '{}'::jsonb,resultado JSONB,estado TEXT NOT NULL DEFAULT 'propuesta' CHECK(estado IN ('propuesta','aprobada','rechazada','ejecutada','error','cancelada','expirada')),requiere_aprobacion BOOLEAN NOT NULL DEFAULT FALSE,error TEXT,creada_at TIMESTAMPTZ NOT NULL DEFAULT now(),resuelta_at TIMESTAMPTZ);
CREATE TABLE IF NOT EXISTS ia_ejecuciones_shell (id_shell UUID PRIMARY KEY DEFAULT gen_random_uuid(),id_llamada UUID REFERENCES ia_llamadas_herramienta(id_llamada) ON DELETE SET NULL,id_ejecucion UUID NOT NULL REFERENCES ia_ejecuciones(id_ejecucion) ON DELETE CASCADE,comando TEXT NOT NULL,argv JSONB NOT NULL DEFAULT '[]'::jsonb,cwd TEXT,stdout TEXT,stderr TEXT,codigo_salida INTEGER,duracion_ms INTEGER,truncado BOOLEAN NOT NULL DEFAULT FALSE,created_at TIMESTAMPTZ NOT NULL DEFAULT now());
CREATE INDEX IF NOT EXISTS idx_ia_conversaciones_usuario ON ia_conversaciones(id_usuario,updated_at DESC); CREATE INDEX IF NOT EXISTS idx_ia_mensajes_conversacion ON ia_mensajes(id_conversacion,created_at);
INSERT INTO ia_configuracion(clave) VALUES('global') ON CONFLICT(clave) DO NOTHING;
INSERT INTO ia_politicas(clave,nombre,prompt_sistema,reglas,dominios_permitidos,dominios_bloqueados,herramientas_habilitadas,comandos_bloqueados) VALUES('predeterminada','PolÃ­tica predeterminada','Eres el asistente de FagoLab. No inventes datos de laboratorio; cita siempre la fuente y resuelve DOI mediante Crossref.','["No inventar datos de laboratorio","Citar siempre la fuente","Resolver DOI vÃ­a Crossref"]','{api.crossref.org,ncbi.nlm.nih.gov,doi.org,nature.com,science.org,sciencedirect.com,springer.com,wiley.com}','{localhost,127.0.0.1,169.254.169.254}','{}','{}') ON CONFLICT(clave) DO UPDATE SET nombre=EXCLUDED.nombre,prompt_sistema=EXCLUDED.prompt_sistema,reglas=EXCLUDED.reglas,dominios_permitidos=EXCLUDED.dominios_permitidos,dominios_bloqueados=EXCLUDED.dominios_bloqueados;
INSERT INTO ia_conectores(clave,nombre,tipo,base_url,habilitado) VALUES('brave','Brave Search','busqueda_web','https://api.search.brave.com',FALSE),('crossref','Crossref','doi','https://api.crossref.org',TRUE) ON CONFLICT(clave) DO UPDATE SET nombre=EXCLUDED.nombre,tipo=EXCLUDED.tipo,base_url=EXCLUDED.base_url,habilitado=EXCLUDED.habilitado;
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname='trg_ia_configuracion_updated_at') THEN CREATE TRIGGER trg_ia_configuracion_updated_at BEFORE UPDATE ON ia_configuracion FOR EACH ROW EXECUTE FUNCTION set_updated_at(); END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname='trg_ia_conversaciones_updated_at') THEN CREATE TRIGGER trg_ia_conversaciones_updated_at BEFORE UPDATE ON ia_conversaciones FOR EACH ROW EXECUTE FUNCTION set_updated_at(); END IF;
END $$;
