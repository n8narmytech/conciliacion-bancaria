/**
 * Orquesta el flujo de las tres pantallas y guarda el estado de la sesión.
 *
 * La conciliación se ejecuta una sola vez al pasar de "carga" a "ajustes":
 * el resultado queda en memoria y todo el trabajo del carrito (agregar y
 * quitar ajustes) se recalcula en el cliente sin volver al servidor. Solo
 * se vuelve a llamar a la API al final, para generar el Excel con los
 * ajustes ya aplicados.
 */

"use client";

import { useEffect, useState } from "react";
import { conciliar, descargarExcel, obtenerBancos } from "@/lib/api";
import { nombreArchivoReporte } from "@/lib/formato";
import type { Ajuste, Banco, RespuestaConciliacion } from "@/lib/tipos";
import { PantallaCarga } from "@/components/PantallaCarga";
import { PantallaCarrito } from "@/components/PantallaCarrito";
import { PantallaResultado } from "@/components/PantallaResultado";
import { Aviso } from "@/components/ui";

type Pantalla = "carga" | "ajustes" | "resultado";

interface ArchivosCargados {
  banco: string;
  archivoCrm: File;
  archivoBanco: File;
  saldoApertura: number;
}

const PASOS: { id: Pantalla; nombre: string }[] = [
  { id: "carga", nombre: "Archivos" },
  { id: "ajustes", nombre: "Ajustes" },
  { id: "resultado", nombre: "Resultado" },
];

export default function Home() {
  const [bancos, setBancos] = useState<Banco[]>([]);
  const [errorBancos, setErrorBancos] = useState<string | null>(null);

  const [pantalla, setPantalla] = useState<Pantalla>("carga");
  const [archivos, setArchivos] = useState<ArchivosCargados | null>(null);
  const [resultado, setResultado] = useState<RespuestaConciliacion | null>(null);
  const [ajustes, setAjustes] = useState<Ajuste[]>([]);

  const [procesando, setProcesando] = useState(false);
  const [generando, setGenerando] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    obtenerBancos()
      .then(setBancos)
      .catch((e) => setErrorBancos(e.message));
  }, []);

  const ejecutar = async (params: ArchivosCargados) => {
    setProcesando(true);
    setError(null);
    try {
      // Primera corrida sin ajustes: da la diferencia bruta a explicar.
      const res = await conciliar({ ...params, ajustes: [] });
      setResultado(res);
      setArchivos(params);
      setAjustes([]);
      setPantalla("ajustes");
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setProcesando(false);
    }
  };

  const finalizar = async () => {
    if (!archivos) return;
    setGenerando(true);
    setError(null);
    try {
      // Segunda y última llamada: con los ajustes aplicados y pidiendo el Excel.
      const res = await conciliar({ ...archivos, ajustes, incluirExcel: true });
      setResultado(res);
      setPantalla("resultado");
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setGenerando(false);
    }
  };

  const empezarDeNuevo = () => {
    setPantalla("carga");
    setArchivos(null);
    setResultado(null);
    setAjustes([]);
    setError(null);
  };

  const nombreBanco =
    bancos.find((b) => b.codigo === archivos?.banco)?.nombre ?? archivos?.banco ?? "";

  return (
    <main
      style={{
        maxWidth: 860,
        margin: "0 auto",
        padding: "2.5rem 1.25rem 4rem",
        display: "flex",
        flexDirection: "column",
        gap: "1.5rem",
      }}
    >
      <header>
        <h1 style={{ margin: 0, fontSize: "1.5rem", fontWeight: 680, letterSpacing: "-0.01em" }}>
          Conciliación bancaria
        </h1>
        <p style={{ margin: "0.25rem 0 0", fontSize: "0.88rem", color: "var(--tinta-suave)" }}>
          Army Technologies S.A. — libro mayor del CRM contra el extracto del banco
        </p>
      </header>

      <Pasos actual={pantalla} />

      {errorBancos && (
        <Aviso tono="error" titulo="No se pudo conectar con el servidor">
          {errorBancos}
        </Aviso>
      )}

      {pantalla === "carga" && (
        <PantallaCarga
          bancos={bancos}
          onConciliar={ejecutar}
          procesando={procesando}
          error={error}
        />
      )}

      {pantalla === "ajustes" && resultado && (
        <PantallaCarrito
          stats={resultado.estadisticas}
          ajustes={ajustes}
          setAjustes={setAjustes}
          onVolver={() => setPantalla("carga")}
          onFinalizar={finalizar}
          generando={generando}
        />
      )}

      {pantalla === "resultado" && resultado && (
        <PantallaResultado
          stats={resultado.estadisticas}
          ajustes={ajustes}
          discrepancias={resultado.discrepancias}
          nombreBanco={nombreBanco}
          onDescargar={() => {
            if (resultado.excel_base64) {
              descargarExcel(
                resultado.excel_base64,
                nombreArchivoReporte(archivos?.banco ?? "banco"),
              );
            }
          }}
          onVolverCarrito={() => setPantalla("ajustes")}
          onEmpezarDeNuevo={empezarDeNuevo}
        />
      )}
    </main>
  );
}

function Pasos({ actual }: { actual: Pantalla }) {
  const indiceActual = PASOS.findIndex((p) => p.id === actual);
  return (
    <nav
      aria-label="Progreso"
      style={{ display: "flex", alignItems: "center", gap: "0.5rem", flexWrap: "wrap" }}
    >
      {PASOS.map((paso, i) => {
        const hecho = i < indiceActual;
        const activo = i === indiceActual;
        return (
          <div key={paso.id} style={{ display: "flex", alignItems: "center", gap: "0.5rem" }}>
            <span
              style={{
                display: "flex",
                alignItems: "center",
                gap: "0.4rem",
                fontSize: "0.82rem",
                fontWeight: activo ? 650 : 500,
                color: activo
                  ? "var(--acento)"
                  : hecho
                    ? "var(--tinta-media)"
                    : "var(--tinta-suave)",
              }}
            >
              <span
                className="cifra"
                style={{
                  width: 20,
                  height: 20,
                  borderRadius: "50%",
                  display: "inline-flex",
                  alignItems: "center",
                  justifyContent: "center",
                  fontSize: "0.7rem",
                  fontWeight: 650,
                  background: activo
                    ? "var(--acento)"
                    : hecho
                      ? "var(--ok-fondo)"
                      : "var(--superficie-alt)",
                  color: activo ? "#fff" : hecho ? "var(--ok)" : "var(--tinta-suave)",
                  border: `1px solid ${activo ? "var(--acento)" : hecho ? "var(--ok-borde)" : "var(--borde)"}`,
                }}
              >
                {hecho ? "✓" : i + 1}
              </span>
              {paso.nombre}
            </span>
            {i < PASOS.length - 1 && (
              <span style={{ width: 22, height: 1, background: "var(--borde-fuerte)" }} />
            )}
          </div>
        );
      })}
    </nav>
  );
}
