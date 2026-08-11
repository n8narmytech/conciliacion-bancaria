/**
 * Formateo de cifras y fechas para la interfaz.
 *
 * Todo el sistema muestra pesos argentinos con separador de miles "." y
 * decimal ",". Se centraliza acá para que no haya dos formatos distintos
 * conviviendo en pantalla.
 */

const FORMATO_PESOS = new Intl.NumberFormat("es-AR", {
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});

/** "$ 1.234.567,89" — con signo explícito cuando es negativo. */
export function pesos(monto: number): string {
  const signo = monto < 0 ? "-" : "";
  return `${signo}$ ${FORMATO_PESOS.format(Math.abs(monto))}`;
}

/** Igual que `pesos` pero fuerza el "+" en los positivos, para listas de ajustes. */
export function pesosConSigno(monto: number): string {
  if (monto >= 0) return `+$ ${FORMATO_PESOS.format(monto)}`;
  return `-$ ${FORMATO_PESOS.format(Math.abs(monto))}`;
}

/** "2026-06-30" → "30/06" */
export function fechaCorta(iso: string | null): string {
  if (!iso) return "—";
  const partes = iso.split("-");
  if (partes.length < 3) return iso;
  return `${partes[2]}/${partes[1]}`;
}

/** "2026-06-30" → "30/06/2026" */
export function fechaLarga(iso: string | null): string {
  if (!iso) return "—";
  const partes = iso.split("-");
  if (partes.length < 3) return iso;
  return `${partes[2]}/${partes[1]}/${partes[0]}`;
}

/**
 * Tolerancia con la que se considera cerrada una conciliación.
 * Las conciliaciones históricas del equipo cierran entre $0 y $1.500.
 */
export const TOLERANCIA_CIERRE = 1500;

export function cierraOk(diferencia: number): boolean {
  return Math.abs(diferencia) <= TOLERANCIA_CIERRE;
}

/** Nombre sugerido para el Excel descargado. */
export function nombreArchivoReporte(banco: string): string {
  const ahora = new Date();
  const p = (n: number) => String(n).padStart(2, "0");
  const sello = `${ahora.getFullYear()}${p(ahora.getMonth() + 1)}${p(ahora.getDate())}_${p(ahora.getHours())}${p(ahora.getMinutes())}`;
  return `conciliacion_${banco}_${sello}.xlsx`;
}
