# AGENTS — fago-api (backend)

> Contrato de trabajo para agentes y humanos sobre **fago-api**, el backend de la
> app de fagoterapia acuícola de Pamela. Léelo antes de tocar código.
> Repo gemelo (frontend): `../fago/AGENTS.md`.

## 1. Qué es esto

Digitalización del experimento de microbiología/acuicultura de Pamela (fagoterapia
sobre bacterias de peces enfermos). El objetivo de producto es reemplazar su **Excel**
de muestreo por una app trazable de extremo a extremo (recepción del pez → cajas Petri
→ subcultivo → ADN → PCR → gel).

**Núcleo del Excel (no perderlo de vista):** una fila del Excel = `Muestra` (lote +
órgano + réplica) · `Medio` · `Descripción de Colonia`. En el modelo eso es
**caja_petri + su observación de colonia** (`observaciones_caja_petri`). Ese es el dato
que la doctora realmente registra; todo lo demás lo envuelve.

## 2. Arquitectura

| Componente | Repo / app | Stack |
|---|---|---|
| Frontend | `fago` | Vue 3 + TS + Vite + Pinia, router hash |
| Backend | `fago-api` (este) | FastAPI, Python 3.11 (Docker `python:3.11-slim`) |
| BD | `fago-bd-postgres` | PostgreSQL 16, 40 tablas de dominio, PK UUID, JSONB, triggers |

Todo desplegado en **Kaanbal Engine** (PaaS del usuario sobre Kubernetes):
GitHub Actions → Docker Hub (`andresupmh/<app>`) → `infra-gitops` (overlays kustomize
dev/prod) → ArgoCD. Org: `futurefarms-softwarefactory`.

### Entornos

| Entorno | Rama | API | Postgres |
|---|---|---|---|
| local | (working tree) | `http://localhost:8000` (uvicorn) | contenedor local (`docker-compose.local.yml`, puerto 5433) o dev vía Tailscale |
| dev | `develop` | `https://dev-fago-api.futurefarms.mx` | `dev-fago-bd-postgres` |
| prod | `main` | `https://fago-api.futurefarms.mx` | prod (solo API prod) |

La BD **dev** está expuesta por Tailscale y alimenta **tanto** la API dev desplegada
**como** el entorno local. La BD **prod** solo se comunica con la API prod.

## 3. Flujo de trabajo (contrato del proyecto)

```
local (listo y verificado)  →  push a develop (dev)  →  merge manual develop→main (prod)
```

1. **Local**: se trabaja con `uvicorn` (backend) + `vite` (frontend), ambos apuntando a
   la **BD dev** por Tailscale. Es el ciclo rápido de iteración.
2. **Antes de hacer `git push`**: construir la **imagen Docker** localmente para validar
   que compila y arranca (`docker build`). No se sube código que no construya imagen.
3. **Push a `develop`** → CI → Docker Hub → infra-gitops (overlay dev) → ArgoCD → dev.
4. **Merge manual `develop` → `main`** (lo hace el usuario) → mismo pipeline → prod.

> Regla heredada de la plataforma: **el trabajo no se cierra solo con ediciones locales.**
> Una entrega a dev/prod está completa cuando: push → CI verde → infra-gitops actualizado
> → Argo Synced/Healthy → validación en runtime (`GET /health` 200, BD conectada).

## 4. Setup local

```bash
# requisitos: Python 3.x (usar `py -3` en Windows; el alias `python` está roto vía MS Store)
cd "fago-api"
py -3 -m pip install -r requirements.txt

# Opción A — Postgres local en Docker (no depende de Tailscale):
docker compose -f docker-compose.local.yml up -d   # postgres:16 en localhost:5433
py -3 db/migrate.py                                # aplica 001 + migraciones
py -3 db/bootstrap_admin.py                        # crea la primera superadministradora

# Opción B — credenciales de la BD dev: _private/.env.dev (GITIGNORED — nunca commitear)
#   POSTGRES_HOST=dev-fago-bd-postgres.tail11dd4e.ts.net
#   POSTGRES_PORT=5432  POSTGRES_DB=fago_bd_postgres  POSTGRES_USER=devadmin  POSTGRES_PASSWORD=...

# arrancar
py -3 -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
curl http://localhost:8000/health   # {"status":"healthy","db":true}
```

Antes de push: `docker build -t fago-api:local .` debe pasar.

## 5. Mapa del código

- `app/main.py` — FastAPI, CORS (orígenes + regex localhost para cualquier puerto de Vite),
  monta `/static` y `/media`, `/health`, `/` y `/ws/presence` autenticado.
- `app/config.py` — `Settings` desde env. Acepta `POSTGRES_*` (local) o el alias de Kaanbal
  `FAGO_BD_POSTGRES_*`. `_load_local_env()` carga `_private/.env.dev` solo en local.
- `app/db.py` — `ConnectionPool` (psycopg 3, `dict_row`), `get_conn()`, helpers.
- `app/repo.py` — lecturas + escrituras del esqueleto (recepción, pez, muestras, cajas,
  observación) + helpers de códigos.
- `app/repo_mol.py` — cadena molecular (subcultivo→extracción→vial→nanodrop→pcr→gel) + media.
- `app/repo_seq.py` + `app/seq_api.py` — secuenciación (`/api/secuenciaciones`): candidatos del gel,
  alta con procedencia declarada y archivos FASTQ/FASTA.
- `app/seq_qc.py` — validación y QC **deterministas** de FASTA/FASTQ (sin IA): formato, longitud,
  %GC, bases ambiguas, N50; en FASTQ además calidad Phred, %Q20/%Q30 y muestreo de lecturas.
- `app/tools/` — **catálogo de herramientas** (10). `base.py` define el contrato (firma, esquema
  de parámetros, permiso, plano de ejecución); `secuencia.py`, `ncbi.py`, `pubmed.py`,
  `laboratorio.py` y `filogenia.py` las registran; `__main__.py` es la puerta de línea de comandos.
  Una implementación, cuatro puertas: API, agente, CLI y orquestador externo.
  `py -3 -m app.tools --catalogo`. La CLI fuerza UTF-8 en stdout: sin eso, una consola
  Windows (cp1252) revienta con los abstracts de PubMed.
- `app/tools/filogenia.py` — árbol **orientativo** por distancias de k-meros + neighbor-joining,
  en Python puro. No es una filogenia publicable (sin alineamiento múltiple, sin modelo evolutivo,
  sin bootstrap) y la limitación viaja en la propia salida de la herramienta.
- `app/repo_fichas.py` — **el único paso generativo**. Reúne la evidencia (trazabilidad, QC,
  hits, taxonomía, PubMed), la formatea, llama al modelo y guarda la ficha junto con sus
  parámetros y el `sha256` de la evidencia. `conEvidencia=False` genera el control del
  experimento: la misma pregunta sin nada en qué apoyarse. `lanzar_experimento` genera una
  serie de fichas variando **una sola** variable (temperatura, top_p, seed, con/sin evidencia)
  para poder compararlas.
- `app/repo_analisis.py` + `app/analisis_api.py` — corridas de análisis. BLAST es asíncrono
  (NCBI devuelve un RID), así que corre en un hilo que actualiza `corridas_analisis` y la UI
  consulta el estado. Los hits van a `resultados_blast` y la evidencia a `evidencias_externas`.
- `app/api.py` — router `/api` (todas las rutas REST + `POST /api/media`).
- `app/auth_api.py` — autenticación, perfil personal, avatar/portada y administración
  de usuarios, roles, grupos, sesiones y auditoría.
- `app/realtime.py` — presencia WebSocket autenticada, validación de origen, heartbeat
  y base extensible para chat/eventos.
- `db/001_schema.sql` — DDL base de 38 tablas; las migraciones completan las 40 tablas de dominio.
- `db/002_excel_core.sql` — ajuste núcleo Excel (columna `replica`, medios/lotes default, PCR-16S).
- `db/migrate.py` — aplica `001` en una BD vacía y registra/aplica en orden las migraciones
  `NNN_*.sql` pendientes. La imagen Docker lo ejecuta antes de iniciar Uvicorn.
- `db/016_superadmin_guard.sql` — impide degradar/eliminar al último superadmin y
  conserva seguridad/auditoría al limpiar datos experimentales.
- `db/017_chat.sql` — conversaciones directas/grupales, mensajes, reacciones y marcas de lectura.
- `db/018_tareas.sql` — espacios, tablero, flujo configurable, campos, actividad y esquemas de permisos.
- `db/019_ia_agente.sql` — configuración cifrada, políticas, conectores, conversaciones y ejecuciones IA.
- `db/023_secuenciacion.sql` — secuenciación abierta (no exige PCR), `archivos_secuencia` y
  `evidencias_externas`. Introduce `origen_dato` (experimental | publico_ncbi | sintetico).
- `db/024_analisis.sql` — `corridas_analisis` con estado, progreso y referencia externa (RID).
- `db/025_fichas.sql` — `fichas_analisis`: texto, secciones, parámetros de generación
  (modelo, temperatura, top_p, seed) y hash de la evidencia. Reproducibilidad y comparación.
- `app/repo_chat.py` + `app/chat_api.py` — autoridad HTTP del chat; WebSocket solo difunde eventos.
- `app/repo_tareas.py` + `app/tareas_api.py` + `app/tareas_workflow.py` — tareas y motor de reglas.
- `app/repo_ia.py` + `app/ia_api.py` + `app/agent/` — agente, herramientas filtradas por ACL,
  DeepSeek, conectores web y shell local con aprobación.
- `db/seed_excel.py` — siembra idempotente derivada del Excel (no duplica si ya hay peces).

## 6. Convenciones (no regresar)

1. **camelCase desde SQL**: las consultas usan ALIAS (`id_recepcion::text as "idRecepcion"`)
   para devolver exactamente la forma que consume el frontend. **No** se añade capa de mapeo.
   Si cambias una columna, mantén el alias estable o coordina con `../fago/src/data/types.ts`.
2. **Escrituras en transacción**: cada objeto rastreable se inserta primero en
   `objetos_laboratorio` (identidad + código único) y luego en su tabla específica + etiqueta.
3. **UX de la científica primero**: el backend resuelve solo los detalles de laboratorio
   (lote de medio por defecto, creación de colonia al subcultivar) para que Pamela/la doctora
   no gestionen entidades técnicas a mano. Mantener esa simplicidad al añadir endpoints.
4. **Campos extra = opcionales**: el esquema completo está instalado; los campos más allá del
   núcleo del Excel son opcionales (sirven para modelos de IA futuros). No hacerlos obligatorios.
5. **Códigos legibles**: `REC-…`, `PEZ-NNN`, `MB-PEZ-NNN-<org>`, `CP-PEZ-NNN-NNN`, etc.
   Alimentan QR + código de barras en el frontend.
6. **Esquema versionado siempre**: cualquier cambio de código que lea o escriba una
   tabla/columna nueva debe incluir en el mismo commit: (a) el estado final en
   `db/001_schema.sql`, (b) una migración incremental nueva `db/NNN_descripcion.sql`
   idempotente para bases existentes y (c) la actualización de este mapa si cambia el
   modelo o el flujo. Antes de cerrar el trabajo, ejecutar `py -3 db/migrate.py` contra
   la BD local y verificar `/health` y al menos una ruta que use el cambio.
7. **Colaboración no es dato experimental**: las tablas de chat, tareas e IA deben permanecer en
   `admin.COLABORACION_TABLES`. Nunca entran en limpiar/sembrar/guardar semilla; en particular,
   ninguna llave cifrada puede llegar a `db/seed_data.sql`.
8. **Tiempo real de un solo proceso**: `RealtimeBus` usa memoria; arrancar Uvicorn con `--workers 1`.
   Toda mutación se hace por HTTP con ACL/CSRF y luego se publica en el socket compartido.
9. **Dos capas para tareas**: el permiso efectivo es ACL global **y** esquema contextual del espacio.
   El esquema solo restringe; jamás concede algo negado por ACL.
10. **Agente fail-closed**: solo se serializan al proveedor las herramientas permitidas para el usuario
    y modo. Toda herramienta mutante requiere aprobación; el shell exige además superadmin, kill-switch,
    entorno permitido, opt-in de conversación y auditoría doble.
11. **Procedencia del dato, siempre explícita**: toda secuencia y toda evidencia declara si es
    `experimental`, `publico_ncbi` o `sintetico`. Un dato descargado de NCBI jamás se presenta como
    resultado del laboratorio, ni en la API ni en la UI ni en una ficha generada por el modelo.
12. **La IA no calcula hechos**: validación, QC, BLAST y taxonomía producen datos objetivos y se
    guardan tal cual. El modelo generativo solo interpreta y redacta a partir de ellos, y su salida
    se marca como generada por IA.
13. **Toda capacidad nueva entra como herramienta del catálogo**, con firma, esquema de parámetros,
    permiso propio y plano de ejecución declarado. Nunca lógica suelta dentro de un endpoint: el
    catálogo es lo que consumen por igual la API, el agente, la CLI y cualquier orquestador externo.
    `POST /api/tools/{nombre}` aplica doble candado — ACL de la ruta **y** permiso de la herramienta.

## 5.1 Trampa conocida de NCBI BLAST

La base 16S en la URL API se llama `rRNA_typestrains/16S_ribosomal_RNA`, **no**
`16S_ribosomal_RNA` (que es el nombre de la interfaz web). Con el nombre corto el servicio
responde `ThereAreHits=no` sin error: una secuencia 16S perfecta parece no tener coincidencias.
`app/tools/ncbi.py` acepta los alias `16S` y `nt` justamente para no volver a caer en eso.

## 7. Seguridad (obligatorio)

1. **Nunca** commitear secretos/tokens/llaves en texto plano. `_private/`, `.env`, `.env.*`
   están en `.gitignore` (excepto `.env.example`).
2. No pegar credenciales en docs, commits ni logs. Si se filtra una, rotarla de inmediato.
3. Trabajar solo contra repos de la org `futurefarms-softwarefactory`.
4. Semillas, limpiezas y restores desde la interfaz nunca incluyen autenticación,
   ACL, sesiones ni auditoría; el último superadmin se protege también en PostgreSQL.

## 8. API (resumen)

Lecturas `GET /api/`: `proyecto, dashboard, recepciones, peces, muestras, cajas,
subcultivos, extracciones, viales, nanodrop, pcr, geles, etiquetas`.

Escrituras `POST /api/`: `recepciones`, `aislamiento` (pez + muestras×órgano +
cajas×medio×réplicas), `cajas/{id}/observacion` (**núcleo Excel**),
`cajas/{id}/subcultivo`, `subcultivos/{id}/extraccion`, `viales/{id}/nanodrop`,
`viales/{id}/pcr`, `geles`, `media` (multipart) + `GET /api/media/objeto/{codigo}`.
