# Serrate Paz & Asociados — Inteligencia Comercial y Prospección

Aplicación web para identificar y gestionar como prospectos a **constructoras, desarrolladoras y promotoras
inmobiliarias y empresas de infraestructura en Bolivia** (prioridad: Santa Cruz, La Paz y Cochabamba).

- Base de datos persistente con **fuente, fecha y nivel de confianza** para cada dato.
- **Investigación automática** en fuentes públicas (buscadores, noticias, sitios oficiales) y botón **INVESTIGAR MÁS**.
- **Análisis comercial** con puntuación 1–100 explicada, necesidades jurídicas, riesgos y motivo de acercamiento.
- **GENERAR CONTACTO**: correo, mensaje de LinkedIn, propuesta de reunión y argumento comercial, basados en hechos
  reales. **Nunca se envía nada automáticamente.**
- **CRM** con los 10 estados del embudo, comentarios, reuniones y fechas de seguimiento.
- **Actualización semanal** automática con historial de qué cambió y cuándo.
- **Exportación a Excel** (una hoja por entidad, con fuentes y confianza).
- **Carga de documentos**: planillas Excel/CSV de clientes, PDF y fotos (tarjetas, folletos) para actualizar fichas,
  con vista previa y confirmación; el archivo queda como fuente de cada dato.
- Diseño basado en la presentación institucional 2026 del estudio.

Arquitectura y reglas de calidad: [`docs/ARQUITECTURA.md`](docs/ARQUITECTURA.md).

## Instalación

Requisitos: Python 3.10 o superior.

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate      macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt   # (para pruebas: requirements-dev.txt)
python run.py
```

Abra <http://127.0.0.1:5000>. En Windows también puede usar `iniciar.bat`; en macOS/Linux, `./iniciar.sh`.

Los datos se guardan en `data/serrate_bi.db` (o en la carpeta indicada por `SERRATE_BI_DATA`). Para respaldar,
copie ese archivo o use **Exportar a Excel**.

## Integraciones opcionales (variables de entorno)

| Variable | Para qué |
|---|---|
| `ANTHROPIC_API_KEY` | Búsqueda asistida con Claude (búsqueda web del servidor + verificación) y redacción de contactos. Modelo por defecto `claude-opus-5-5` (cambiar con `SERRATE_BI_CLAUDE_MODEL`). |
| `BRAVE_API_KEY` | Buscador web Brave Search (más estable que el buscador gratuito). |
| `GOOGLE_CSE_KEY` + `GOOGLE_CSE_CX` | Google Programmable Search. |
| (Tesseract instalado + `pip install pytesseract`) | Alternativa local para leer fotos sin Claude. |

Sin claves la aplicación funciona con DuckDuckGo, Google News y Bing News, y la lectura de sitios oficiales.

## Uso típico

1. **Investigación → Buscar nuevas empresas**: elija ciudades y sectores. Se crean prospectos sin duplicados.
2. Abra una ficha: revise datos, fuentes, faltantes. Pulse **INVESTIGAR MÁS** para completar. Corrija o confirme
   datos con «editar» (indique fuente y confianza); los valores anteriores quedan en el historial.
3. Revise **Análisis comercial** y pulse **GENERAR CONTACTO**. Edite, **apruebe**, envíe desde su correo
   («Abrir en mi correo») o LinkedIn, y **marque como enviado**: el CRM pasa a «Contactado».
4. Registre llamadas, reuniones y fechas de seguimiento en **Seguimiento**. El panel muestra pendientes.
5. **Documentos**: suba una planilla de clientes (se reconocen columnas como Empresa, Ciudad, Correo, Teléfono,
   Gerente, Cargo, Notas), un PDF o la foto de una tarjeta. Revise la vista previa, elija qué incorporar y con qué
   confianza. Las fotos y PDF escaneados requieren `ANTHROPIC_API_KEY` (o Tesseract instalado) para leerse.
6. **Actualizaciones** muestra los datos modificados, proyectos y noticias nuevas de cada semana.

## Línea de comandos (programar con cron / Programador de tareas)

```bash
python -m serrate_bi.cli semanal                      # actualización semanal completa
python -m serrate_bi.cli descubrir --ciudades "Santa Cruz,La Paz,Cochabamba"
python -m serrate_bi.cli investigar 12                # INVESTIGAR MÁS sobre la empresa 12
python -m serrate_bi.cli exportar prospectos.xlsx
```

## Pruebas

```bash
pip install -r requirements-dev.txt
pytest -q
```

Las pruebas usan una «internet simulada» con empresas y dominios **ficticios** (`.test`) para verificar de punta a
punta: extracción, reglas de confianza, deduplicación, historial de cambios, investigación, descubrimiento,
análisis, generación de contactos (sin mensajes genéricos ni menciones a conflictos ante el prospecto), CRM,
programador semanal, persistencia y exportación a Excel.

## Próximos pasos sugeridos

- Inicio de sesión y roles si se usará en red por varios abogados.
- Integración con registros oficiales (SEPREC, SICOES) mediante sus canales autorizados.
- Envío asistido desde la cuenta de correo del estudio (siempre con aprobación explícita).
