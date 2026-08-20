/**
 * Cliente de la API de conciliación.
 *
 * Concentra las llamadas HTTP en un solo lugar para que los componentes
 * no manejen fetch ni FormData directamente.
 */

import type {
  Banco,
  ParametrosConciliacion,
  RespuestaConciliacion,
} from "./tipos";

/** Error de la API con el detalle que devolvió el backend. */
export class ErrorApi extends Error {
  constructor(
    message: string,
    public readonly status: number,
  ) {
    super(message);
    this.name = "ErrorApi";
  }
}

async function leerError(res: Response): Promise<string> {
  try {
    const data = await res.json();
    // FastAPI devuelve el mensaje en `detail`
    if (typeof data?.detail === "string") return data.detail;
    if (typeof data?.error === "string") return data.error;
    return JSON.stringify(data);
  } catch {
    return res.statusText || `Error ${res.status}`;
  }
}

export async function obtenerBancos(): Promise<Banco[]> {
  const res = await fetch("/api/bancos");
  if (!res.ok) throw new ErrorApi(await leerError(res), res.status);
  const data = await res.json();
  return data.bancos;
}

export async function conciliar(
  params: ParametrosConciliacion,
): Promise<RespuestaConciliacion> {
  const form = new FormData();
  form.append("banco", params.banco);
  form.append("archivo_crm", params.archivoCrm);
  form.append("archivo_banco", params.archivoBanco);
  form.append("saldo_extracto_anterior", String(params.saldoExtractoAnterior));
  form.append("ajustes", JSON.stringify(params.ajustes));
  form.append("incluir_excel", String(params.incluirExcel ?? false));
  if (params.saldoExtracto !== undefined && params.saldoExtracto !== null) {
    form.append("saldo_extracto", String(params.saldoExtracto));
  }

  const res = await fetch("/api/conciliar", { method: "POST", body: form });
  if (!res.ok) throw new ErrorApi(await leerError(res), res.status);
  return res.json();
}

/** Dispara la descarga del .xlsx que devolvió la API en base64. */
export function descargarExcel(base64: string, nombreArchivo: string): void {
  const binario = atob(base64);
  const bytes = new Uint8Array(binario.length);
  for (let i = 0; i < binario.length; i++) bytes[i] = binario.charCodeAt(i);

  const blob = new Blob([bytes], {
    type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
  });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = nombreArchivo;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}
