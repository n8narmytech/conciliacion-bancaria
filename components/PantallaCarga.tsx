/**
 * Pantalla 1 — Carga de archivos y saldos.
 *
 * Elegir banco, subir el libro mayor del CRM y el extracto bancario, y
 * confirmar el saldo de apertura antes de correr la conciliación.
 */

"use client";

import { useEffect, useRef, useState } from "react";
import type { Banco } from "@/lib/tipos";
import { pesos } from "@/lib/formato";
import { Aviso, Boton, CampoNumero, Panel } from "./ui";

/**
 * Último saldo de extracto conocido por banco, solo como referencia rápida.
 *
 * El sistema ya no pide el "saldo de apertura": lo deriva a partir del saldo
 * con que cerró el extracto del mes anterior y del saldo con que abre el
 * libro mayor. Estos valores son los cierres de junio 2026 y sirven para
 * arrancar la conciliación de julio sin ir a buscar el extracto anterior.
 */
const ULTIMO_CIERRE_CONOCIDO: Record<string, { saldo: number; periodo: string }> = {
  bbva: { saldo: 199004.24, periodo: "junio 2026" },
  santander: { saldo: 37245619.46, periodo: "junio 2026" },
  galicia: { saldo: 596813.79, periodo: "junio 2026" },
};

export function PantallaCarga({
  bancos,
  onConciliar,
  procesando,
  error,
}: {
  bancos: Banco[];
  onConciliar: (params: {
    banco: string;
    archivoCrm: File;
    archivoBanco: File;
    saldoExtractoAnterior: number;
  }) => void;
  procesando: boolean;
  error: string | null;
}) {
  const [banco, setBanco] = useState<string>("");
  const [archivoCrm, setArchivoCrm] = useState<File | null>(null);
  const [archivoBanco, setArchivoBanco] = useState<File | null>(null);
  const [saldoExtractoAnterior, setSaldoExtractoAnterior] = useState<number>(0);
  const [cierreTocado, setCierreTocado] = useState(false);

  // Al elegir banco, precargar el último cierre conocido como referencia
  useEffect(() => {
    if (banco && !cierreTocado) {
      setSaldoExtractoAnterior(ULTIMO_CIERRE_CONOCIDO[banco]?.saldo ?? 0);
    }
  }, [banco, cierreTocado]);

  useEffect(() => {
    if (!banco && bancos.length > 0) setBanco(bancos[0].codigo);
  }, [bancos, banco]);

  const bancoElegido = bancos.find((b) => b.codigo === banco);
  const referencia = ULTIMO_CIERRE_CONOCIDO[banco];
  const listo = Boolean(banco && archivoCrm && archivoBanco);

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "1.25rem" }}>
      {error && (
        <Aviso tono="error" titulo="No se pudo procesar">
          {error}
        </Aviso>
      )}

      <Panel
        titulo="Banco a conciliar"
        descripcion="Cada banco tiene su propia lógica de matching y sus ajustes automáticos."
      >
        <div style={{ display: "flex", gap: "0.6rem", flexWrap: "wrap" }}>
          {bancos.map((b) => {
            const activo = b.codigo === banco;
            return (
              <button
                key={b.codigo}
                onClick={() => setBanco(b.codigo)}
                style={{
                  padding: "0.6rem 1.1rem",
                  borderRadius: 6,
                  border: `1px solid ${activo ? "var(--acento)" : "var(--borde-fuerte)"}`,
                  background: activo ? "var(--acento-suave)" : "var(--superficie)",
                  color: activo ? "var(--acento)" : "var(--tinta-media)",
                  fontWeight: activo ? 650 : 500,
                  fontSize: "0.9rem",
                  cursor: "pointer",
                }}
              >
                {b.nombre}
              </button>
            );
          })}
        </div>

        {bancoElegido && bancoElegido.formatos.length > 0 && (
          <p
            style={{
              margin: "0.85rem 0 0",
              fontSize: "0.79rem",
              color: "var(--tinta-suave)",
            }}
          >
            Formato esperado: {bancoElegido.formatos.join(" · ")}
          </p>
        )}
      </Panel>

      <Panel
        titulo="Archivos del período"
        descripcion="El libro mayor exportado del CRM y el extracto que descargaste del banco."
      >
        <div
          style={{
            display: "grid",
            gridTemplateColumns: "repeat(auto-fit, minmax(240px, 1fr))",
            gap: "0.9rem",
          }}
        >
          <ZonaArchivo
            etiqueta="Libro mayor (CRM)"
            archivo={archivoCrm}
            onArchivo={setArchivoCrm}
          />
          <ZonaArchivo
            etiqueta="Extracto bancario"
            archivo={archivoBanco}
            onArchivo={setArchivoBanco}
          />
        </div>
      </Panel>

      <Panel
        titulo="Cierre del mes anterior"
        descripcion="Con este dato el sistema calcula solo el saldo de apertura, comparándolo contra el saldo con que abre el libro mayor."
      >
        <div style={{ maxWidth: 380 }}>
          <CampoNumero
            etiqueta="Saldo del extracto al cierre del mes anterior"
            valor={saldoExtractoAnterior}
            onChange={(v) => {
              setCierreTocado(true);
              setSaldoExtractoAnterior(v);
            }}
            ayuda={
              cierreTocado
                ? "Valor ingresado manualmente."
                : referencia
                  ? `Último cierre registrado de ${bancoElegido?.nombre ?? "este banco"} (${referencia.periodo}): ${pesos(referencia.saldo)}. Cambialo si vas a conciliar otro período.`
                  : "Tomalo del extracto del mes anterior."
            }
          />
        </div>
        <p style={{ margin: "0.75rem 0 0", fontSize: "0.79rem", color: "var(--tinta-suave)" }}>
          Ya no hace falta informar el saldo de apertura: se deriva de este número.
          Si el libro abre en el mismo saldo con que cerró el banco, no hay arrastre;
          si difieren, esa diferencia es lo que quedó pendiente del mes anterior.
        </p>
      </Panel>

      <div style={{ display: "flex", justifyContent: "flex-end" }}>
        <Boton
          variante="primario"
          disabled={!listo || procesando}
          onClick={() =>
            listo &&
            onConciliar({
              banco,
              archivoCrm: archivoCrm!,
              archivoBanco: archivoBanco!,
              saldoExtractoAnterior,
            })
          }
        >
          {procesando ? "Procesando…" : "Conciliar →"}
        </Boton>
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ */

function ZonaArchivo({
  etiqueta,
  archivo,
  onArchivo,
}: {
  etiqueta: string;
  archivo: File | null;
  onArchivo: (f: File) => void;
}) {
  const [encima, setEncima] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  const soltar = (e: React.DragEvent) => {
    e.preventDefault();
    setEncima(false);
    const f = e.dataTransfer.files?.[0];
    if (f) onArchivo(f);
  };

  return (
    <div>
      <span
        className="etiqueta"
        style={{ display: "block", marginBottom: "0.4rem" }}
      >
        {etiqueta}
      </span>
      <div
        onDragOver={(e) => {
          e.preventDefault();
          setEncima(true);
        }}
        onDragLeave={() => setEncima(false)}
        onDrop={soltar}
        onClick={() => inputRef.current?.click()}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === " ") inputRef.current?.click();
        }}
        role="button"
        tabIndex={0}
        style={{
          border: `1.5px dashed ${encima ? "var(--acento)" : archivo ? "var(--ok-borde)" : "var(--borde-fuerte)"}`,
          background: encima
            ? "var(--acento-suave)"
            : archivo
              ? "var(--ok-fondo)"
              : "var(--superficie-alt)",
          borderRadius: 6,
          padding: "1.1rem 0.9rem",
          textAlign: "center",
          cursor: "pointer",
          minHeight: 92,
          display: "flex",
          flexDirection: "column",
          justifyContent: "center",
          gap: "0.25rem",
        }}
      >
        {archivo ? (
          <>
            <strong
              style={{
                fontSize: "0.84rem",
                color: "var(--ok)",
                wordBreak: "break-word",
              }}
            >
              {archivo.name}
            </strong>
            <span style={{ fontSize: "0.75rem", color: "var(--tinta-suave)" }}>
              {(archivo.size / 1024).toFixed(0)} KB · clic para cambiar
            </span>
          </>
        ) : (
          <>
            <span style={{ fontSize: "0.86rem", color: "var(--tinta-media)" }}>
              Arrastrá el archivo acá
            </span>
            <span style={{ fontSize: "0.75rem", color: "var(--tinta-suave)" }}>
              o hacé clic para elegirlo · .xls / .xlsx
            </span>
          </>
        )}
      </div>
      <input
        ref={inputRef}
        type="file"
        accept=".xls,.xlsx"
        hidden
        onChange={(e) => {
          const f = e.target.files?.[0];
          if (f) onArchivo(f);
        }}
      />
    </div>
  );
}
