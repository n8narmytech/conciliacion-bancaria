# Conciliación bancaria

Sistema interno de ARMY TECHNOLOGIES S.A. que concilia el libro mayor del CRM (GBP)
contra los extractos bancarios de **BBVA**, **Santander** y **Galicia**.

Empareja automáticamente lo que corresponde, sugiere los ajustes que puede probar con
los archivos, y deja a la vista solo lo que necesita una decisión contable.

## Cómo se usa

1. **Archivos.** Elegir el banco y subir el libro mayor exportado de GBP y el extracto
   del banco, los dos del mismo mes. No hace falta cargar ningún saldo.
2. **Ajustes.** Aceptar o ignorar las sugerencias del sistema y cargar los ajustes que
   requieren criterio contable. La diferencia se recalcula al instante.
3. **Resultado.** Revisar el cálculo final y los movimientos sin justificar, y
   descargar el Excel.

La conciliación cierra cuando la diferencia final queda entre $0 y **$1.500**.

## Cómo calcula

```
saldo bancario calculado = saldo del libro mayor + saldo de apertura + ajustes
diferencia final         = saldo bancario calculado − saldo del extracto
```

**El saldo de apertura no se carga: se calcula.** Es el desfase que viene del mes
anterior:

```
apertura = saldo del extracto al cierre del mes anterior − saldo con que abre el libro
```

El cierre del mes anterior se obtiene del propio extracto del mes que se concilia:
Santander y Galicia informan el saldo con que abre el período, y BBVA trae el saldo de
cierre y todos los movimientos, así que se deduce restándolos. Está verificado que la
apertura de cada mes coincide al centavo con el cierre del extracto anterior.

> No volver a usar los "saldos de apertura históricos" fijos que figuraban en versiones
> anteriores ($4.374.372,97 en BBVA y $13.983.219,82 en Santander). Contabilidad
> incorporó esas partidas al libro mayor en mayo de 2026: sumarlas otra vez las cuenta
> dos veces.

**Cada movimiento sin pareja individual se clasifica según cómo queda cubierto:**

- **Compensado**: cargos del banco (impuestos, comisiones) que el libro registra en un
  solo asiento agrupado ("- Proveedores"). Se compensan contra ese asiento aunque sobre
  una partida o quede un residuo chico, que se muestra como una sola línea.
- **En un ajuste**: está explicado por un ajuste cargado.
- **Sin justificar**: lo único que hay que revisar.

Antes de calcular, el sistema verifica que el libro mayor y el extracto sean del mismo
mes; si no, frena con un mensaje.

## Bancos

| Banco | Particularidades del perfil |
|---|---|
| BBVA | Cupones Prisma contra liquidaciones de tarjeta, sueldos agrupados por día, diferencia de gastos bancarios, débitos pendientes |
| Santander | Operaciones COMEX, certificaciones agrupadas, cuotas de préstamo, cupones VISA/MASTER, agrupaciones por día, cheques en clearing |
| Galicia | Gastos bancarios agrupados (usa la columna "Grupo de Conceptos" del extracto), débitos pendientes |

Para sumar un banco, crear su perfil en `bancos/` (heredando de `bancos/base.py`) y
registrarlo en `bancos/__init__.py`. Galicia se construyó a partir de dos meses de
archivos.

## Estructura

```
├── app/, components/, lib/   Interfaz (Next.js)
├── api/
│   ├── index.py              API (FastAPI): /api/bancos, /api/conciliar, /api/health
│   ├── _serializacion.py     Conversión del resultado a JSON
│   └── ping.py               Diagnóstico del despliegue, sin dependencias
├── orquestador.py            Coordina una conciliación de punta a punta
├── bancos/                   Un perfil por banco: lectura del extracto, matching y ajustes propios
├── nucleo/
│   ├── carga_crm.py          Lectura del libro mayor de GBP
│   ├── matching_base.py      Emparejamiento común a todos los bancos
│   ├── cobertura.py          Clasificación de los movimientos sin pareja
│   ├── reporte.py            Excel de la conciliación
│   └── ...
├── conciliacion.py           Sistema original; hoy solo se usa clasificar_huerfanos
└── tests/
```

## Desarrollo local

Requisitos: Python 3.9 o superior y Node.js.

```bash
pip3 install -r requirements-dev.txt
npm install
```

Levantar la API y la interfaz, cada una en su terminal:

```bash
python3 -m uvicorn api.index:app --port 8899
```

```bash
npm run dev
```

La interfaz queda en `http://localhost:3000` y, en desarrollo, redirige `/api` a la API
local (ver `next.config.ts`).

## Tests

```bash
python3 -m pytest
```

Correrlos **antes de subir cualquier cambio**. Tardan unos 30 segundos.

- Los que usan los **archivos bancarios reales** (regresión de cada mes, encadenamiento
  de saldos, errores de carga) los leen de `~/Desktop/bancos/BANCO/MES/`, o de la
  carpeta indicada en la variable `CONCILIACION_DATOS`. Los archivos **nunca** se suben
  al repositorio; si faltan, esos tests se saltean.
- Los demás usan datos inventados mínimos y corren en cualquier máquina:
  `python3 -m pytest -m "not datos"`.

**Al cerrar un mes nuevo**: guardar sus archivos en `~/Desktop/bancos/BANCO/MES/`,
agregarlos a `tests/ayudas.py` y sumar sus valores a `tests/test_regresion.py`, pero
solo una vez que el cierre esté validado con contabilidad. Los tests protegen lo que ya
se sabe que está bien; no deciden qué está bien.

Si un test de regresión falla después de un cambio, el cambio alteró el resultado de
una conciliación. Puede ser intencional, pero entonces hay que revisar el caso a mano y
actualizar el valor esperado a conciencia, nunca para que el test pase.

## Despliegue

Vercel, desde la rama `main`. La interfaz se sirve como Next.js y `/api` como una
función Python.

- Las dependencias de la función salen de `requirements.txt`, en la raíz.
- `/api/health` confirma que la API cargó (`{"ok": true, "bancos": 3}`); si falla,
  devuelve el motivo.
- `/api/ping` informa qué archivos y dependencias llegaron al servidor, sin importar
  nada del proyecto. Sirve cuando la API ni siquiera arranca.

Los archivos subidos se procesan en memoria y no se guardan.

## Pendientes con contabilidad

- **Santander**: la comisión de originación del préstamo ($400.500 del 10/06/2026) no
  está incluida en el asiento agrupado de gastos. ¿Se contabiliza aparte?
- **BBVA**: de la transferencia a Diego del 05/06/2026 ($1.950.000) solo está
  contabilizado el movimiento de $1.500.000; falta el de $450.000.
