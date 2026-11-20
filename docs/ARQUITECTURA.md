# Arquitectura y diseño — Serrate Paz BI

Herramienta de inteligencia comercial y prospección para **Serrate Paz & Asociados**. Su objetivo es identificar
constructoras, desarrolladoras y promotoras inmobiliarias y empresas de infraestructura en Bolivia, documentar
cada dato con su fuente, evaluar su potencial como clientes y gestionar el contacto comercial.

## 1. Principios de diseño

| Principio | Cómo se implementa |
|---|---|
| **No inventar datos** | Los extractores sólo toman lo que aparece *literalmente* en la fuente. No se deducen correos por formato, ni nombres, ni teléfonos. Lo que no se encuentra queda como **Pendiente / No disponible**. |
| **Cada dato tiene fuente y fecha** | Toda escritura pasa por `repo.record_fact()`, que exige una fuente y guarda la observación con fecha en la tabla `facts`. |
| **No perder datos** | Las observaciones nunca se borran. Si aparece un valor distinto, queda como *valor alternativo* visible en la ficha; los reemplazos se registran en `change_log`. |
| **Confianza explícita** | `verificado` · `probable` · `no disponible`, con reglas fijas (ver §4). |
| **Sin envíos automáticos** | Los mensajes se generan como **borradores**. El usuario los aprueba, los envía desde su propio correo/LinkedIn y los marca como enviados. |
| **Funciona sin claves de API** | Buscadores y noticias gratuitos por defecto; Claude y Brave/Google son mejoras opcionales. |
| **Persistencia robusta** | SQLite en modo WAL: cada cambio se escribe al instante; un trabajo interrumpido se marca al reiniciar. |

## 2. Componentes

```
┌──────────────────────────── Navegador (dashboard, fichas, CRM) ───────────────────────────┐
│  Jinja2 + CSS propio + JS mínimo (pestañas, seguimiento de trabajos, copiar)              │
└───────────────────────────────────────────┬────────────────────────────────────────────────┘
                                            │ HTTP
┌───────────────────────────────────────────▼────────────────────────────────────────────────┐
│ Flask  (serrate_bi/web.py)                                                                 │
│   Panel · Empresas · Ficha · Investigación · CRM · Actualizaciones · Configuración · Excel │
├──────────────┬───────────────┬────────────────┬───────────────┬──────────────┬─────────────┤
│ analysis.py  │ outreach.py   │ jobs.py        │ export.py     │ repo.py      │ quality.py  │
│ puntuación,  │ borradores de │ cola en 2.º    │ Excel por     │ acceso a     │ confianza,  │
│ necesidades, │ contacto      │ plano +        │ entidad       │ datos y      │ normaliza-  │
│ riesgos      │ (sin envío)   │ semanal        │               │ procedencia  │ ción, dedup │
├──────────────┴───────────────┴───────┬────────┴───────────────┴──────────────┴─────────────┤
│ research/                            │                                                    │
│   pipeline.py   descubrir · INVESTIGAR MÁS · actualización semanal                         │
│   providers.py  Brave / Google CSE / DuckDuckGo · Google News RSS · Bing News RSS          │
│   extract.py    correos, teléfonos, LinkedIn, ejecutivos, proyectos, empresas en noticias  │
│   claude_engine.py  (opcional) Claude + búsqueda web del servidor, con verificación        │
│   http.py       cliente con robots.txt, límite por dominio, identificación                 │
├────────────────────────────────────────────────────────────────────────────────────────────┤
│ SQLite (data/serrate_bi.db, WAL)                                                           │
└────────────────────────────────────────────────────────────────────────────────────────────┘
```

## 3. Modelo de datos (separación de entidades)

| Tabla | Contenido |
|---|---|
| `companies` | Empresa: valores vigentes (nombre, actividad, sector, ciudad, departamento, web, correo, teléfono, LinkedIn, descripción), estado CRM, puntuación y análisis. |
| `executives` | Gerente general, CEO, propietario, socios: nombre, cargo, correo profesional, teléfono, LinkedIn. |
| `sources` | Cada fuente consultada: URL, título, tipo (sitio oficial, noticia, buscador, LinkedIn, manual, búsqueda asistida), primera y última consulta. |
| `facts` | **Todas** las observaciones: entidad, campo, valor, confianza, fuente, evidencia (cita), origen y fecha. Nunca se borran. |
| `field_state` | Valor vigente de cada campo con su confianza, fuente principal y fecha de verificación. |
| `projects` | Edificios, condominios, urbanizaciones, obras: tipo, ciudad, estado, fuente, fecha. |
| `news` | Noticias y actividad reciente, clasificadas (Proyecto, Inversión, Contratación pública, Conflicto/litigio, Financiamiento, Expansión, Regulatorio). |
| `activities` | CRM: comentarios, llamadas, correos, reuniones, cambios de estado, fechas de seguimiento. |
| `outreach_drafts` | Borradores de contacto (correo, LinkedIn, reunión, argumento) con los hechos en que se basan y su estado. |
| `change_log` | Qué dato cambió, cuándo, valor anterior y nuevo, confianza, origen y fuente. |
| `research_runs` | Trabajos de investigación/actualización con log y estadísticas. |

## 4. Reglas de calidad

**Niveles de confianza**
- **Verificado**: publicado en el sitio oficial de la empresa (cuyo contenido menciona a la empresa) o en un registro
  oficial; confirmado por dos fuentes de dominios distintos; o confirmado manualmente.
- **Probable**: una sola fuente de terceros (prensa, buscador, LinkedIn, directorio) o una clasificación por palabras clave.
- **No disponible**: no encontrado → se muestra «Pendiente».

**Fusión de datos (`record_fact`)**
1. Sin valor vigente → se adopta.
2. Mismo valor → se actualiza la fecha de verificación; se eleva la confianza si la nueva fuente es más fuerte o es
   de otro dominio (corroboración).
3. Valor distinto → reemplaza sólo si tiene mayor confianza, o si la **misma fuente** publica otro valor en una
   investigación posterior (la fuente se actualizó). Un dato **manual nunca** se reemplaza automáticamente.
   En otro caso queda como valor alternativo (con botón «adoptar»).

**Deduplicación**: nombre normalizado (sin acentos ni forma societaria), clave central sin palabras genéricas
(«Constructora X S.R.L.» = «X Ltda.»), dominio web y similitud ≥ 0,92.

**Ejecutivos**: sólo cuando una fuente vincula explícitamente nombre + cargo (y la empresa, en fuentes de terceros).
El correo de un ejecutivo sólo se asocia si está publicado en la misma línea que su nombre.

**Búsqueda asistida con Claude** (opcional): cada dato debe traer URL y cita; si la URL no figura entre los
resultados que el propio servidor de búsqueda devolvió, el dato se descarta. Luego la aplicación descarga la página
y comprueba que el valor aparezca para decidir entre verificado y probable.

## 5. Flujos

- **Descubrir**: consultas por ciudad × sector en buscadores + consultas de noticias (proyectos, condominios,
  urbanizaciones, inversiones, obras adjudicadas) → candidatos → deduplicación → alta como «Nuevo prospecto» →
  investigación profunda de las nuevas (opcional) → análisis.
- **INVESTIGAR MÁS**: busca el sitio oficial si falta → lee inicio, contacto, nosotros y proyectos (respetando
  robots.txt) → LinkedIn de la empresa → ejecutivos → noticias → (opcional) Claude → análisis. Los datos nuevos se
  incorporan sin borrar los anteriores.
- **Actualización semanal**: el programador interno revisa cada 30 minutos si pasaron 7 días; reinvestiga (modo
  ligero) todas las empresas activas, busca empresas nuevas y registra los cambios. También disponible por línea
  de comandos para cron/Programador de tareas.
- **Análisis comercial**: puntuación 1–100 = afinidad sectorial (25) + proyectos (20) + actividad reciente (15) +
  detonantes de necesidad jurídica (20) + ubicación prioritaria (10) + posibilidad de contacto (10); necesidades
  jurídicas, riesgos y motivo concreto citando la evidencia.
- **GENERAR CONTACTO**: requiere al menos un proyecto o noticia documentada (si no, se niega: el mensaje sería
  genérico). Los mensajes externos se apoyan en proyectos/inversiones; las noticias de conflicto sólo aparecen en
  el argumento interno, marcadas como sensibles.

## 6. Limitaciones conocidas de la v1

- Los buscadores gratuitos pueden limitar consultas automatizadas; para uso intensivo conviene configurar
  `BRAVE_API_KEY` o Google Programmable Search, o `ANTHROPIC_API_KEY`.
- Sitios construidos íntegramente con JavaScript pueden no exponer su contenido al lector HTML.
- El análisis jurídico es orientativo (reglas); debe validarse con criterio profesional.
- Aplicación monousuario y local (sin inicio de sesión). Para uso en red del estudio, ver «Próximos pasos» en el README.
