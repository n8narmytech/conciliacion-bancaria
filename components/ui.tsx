/**
 * Componentes base compartidos por las tres pantallas.
 *
 * Se mantienen chicos y sin estado: reciben props y renderizan. Todo el
 * color sale de las variables CSS definidas en globals.css.
 */

"use client";

import type { ReactNode } from "react";

/* ------------------------------------------------------------------ */
/* Botón                                                               */
/* ------------------------------------------------------------------ */

type VarianteBoton = "primario" | "secundario" | "fantasma" | "peligro";

export function Boton({
  children,
  onClick,
  variante = "secundario",
  disabled,
  ancho,
  type = "button",
  title,
}: {
  children: ReactNode;
  onClick?: () => void;
  variante?: VarianteBoton;
  disabled?: boolean;
  ancho?: boolean;
  type?: "button" | "submit";
  title?: string;
}) {
  const base: React.CSSProperties = {
    padding: "0.5rem 0.9rem",
    borderRadius: 5,
    fontSize: "0.88rem",
    fontWeight: 550,
    cursor: disabled ? "not-allowed" : "pointer",
    opacity: disabled ? 0.5 : 1,
    width: ancho ? "100%" : undefined,
    transition: "background 120ms ease, border-color 120ms ease",
    border: "1px solid transparent",
    lineHeight: 1.3,
  };

  const estilos: Record<VarianteBoton, React.CSSProperties> = {
    primario: {
      background: "var(--acento)",
      color: "#fff",
      borderColor: "var(--acento)",
    },
    secundario: {
      background: "var(--superficie)",
      color: "var(--tinta)",
      borderColor: "var(--borde-fuerte)",
    },
    fantasma: {
      background: "transparent",
      color: "var(--tinta-media)",
      borderColor: "transparent",
    },
    peligro: {
      background: "transparent",
      color: "var(--error)",
      borderColor: "var(--error-borde)",
    },
  };

  return (
    <button
      type={type}
      onClick={onClick}
      disabled={disabled}
      title={title}
      style={{ ...base, ...estilos[variante] }}
    >
      {children}
    </button>
  );
}

/* ------------------------------------------------------------------ */
/* Panel / tarjeta                                                     */
/* ------------------------------------------------------------------ */

export function Panel({
  children,
  titulo,
  descripcion,
  acciones,
}: {
  children: ReactNode;
  titulo?: string;
  descripcion?: string;
  acciones?: ReactNode;
}) {
  return (
    <section
      style={{
        background: "var(--superficie)",
        border: "1px solid var(--borde)",
        borderRadius: 8,
        boxShadow: "var(--sombra)",
        overflow: "hidden",
      }}
    >
      {(titulo || acciones) && (
        <header
          style={{
            display: "flex",
            alignItems: "flex-start",
            justifyContent: "space-between",
            gap: "1rem",
            padding: "0.9rem 1.1rem",
            borderBottom: "1px solid var(--borde)",
          }}
        >
          <div>
            {titulo && (
              <h2 style={{ margin: 0, fontSize: "0.95rem", fontWeight: 650 }}>
                {titulo}
              </h2>
            )}
            {descripcion && (
              <p
                style={{
                  margin: "0.2rem 0 0",
                  fontSize: "0.82rem",
                  color: "var(--tinta-suave)",
                }}
              >
                {descripcion}
              </p>
            )}
          </div>
          {acciones}
        </header>
      )}
      <div style={{ padding: "1.1rem" }}>{children}</div>
    </section>
  );
}

/* ------------------------------------------------------------------ */
/* Campos de formulario                                                */
/* ------------------------------------------------------------------ */

export function CampoNumero({
  etiqueta,
  ayuda,
  valor,
  onChange,
}: {
  etiqueta: string;
  ayuda?: string;
  valor: number;
  onChange: (v: number) => void;
}) {
  return (
    <label style={{ display: "flex", flexDirection: "column", gap: "0.3rem" }}>
      <span style={{ fontSize: "0.84rem", fontWeight: 550 }}>{etiqueta}</span>
      <input
        type="number"
        step="0.01"
        value={Number.isFinite(valor) ? valor : 0}
        onChange={(e) => onChange(parseFloat(e.target.value) || 0)}
        className="cifra"
        style={{
          padding: "0.5rem 0.65rem",
          border: "1px solid var(--borde-fuerte)",
          borderRadius: 5,
          background: "var(--superficie)",
          color: "var(--tinta)",
          fontSize: "0.9rem",
          textAlign: "right",
          fontFamily: "var(--font-mono)",
        }}
      />
      {ayuda && (
        <span style={{ fontSize: "0.76rem", color: "var(--tinta-suave)" }}>
          {ayuda}
        </span>
      )}
    </label>
  );
}

export function CampoTexto({
  etiqueta,
  valor,
  onChange,
  placeholder,
}: {
  etiqueta: string;
  valor: string;
  onChange: (v: string) => void;
  placeholder?: string;
}) {
  return (
    <label style={{ display: "flex", flexDirection: "column", gap: "0.3rem" }}>
      <span style={{ fontSize: "0.84rem", fontWeight: 550 }}>{etiqueta}</span>
      <input
        type="text"
        value={valor}
        placeholder={placeholder}
        onChange={(e) => onChange(e.target.value)}
        style={{
          padding: "0.5rem 0.65rem",
          border: "1px solid var(--borde-fuerte)",
          borderRadius: 5,
          background: "var(--superficie)",
          color: "var(--tinta)",
          fontSize: "0.9rem",
        }}
      />
    </label>
  );
}

/* ------------------------------------------------------------------ */
/* Estado / avisos                                                     */
/* ------------------------------------------------------------------ */

export function Aviso({
  tono,
  titulo,
  children,
}: {
  tono: "ok" | "alerta" | "error" | "info";
  titulo?: string;
  children?: ReactNode;
}) {
  const mapa = {
    ok: { fondo: "var(--ok-fondo)", borde: "var(--ok-borde)", texto: "var(--ok)" },
    alerta: {
      fondo: "var(--alerta-fondo)",
      borde: "var(--alerta-borde)",
      texto: "var(--alerta)",
    },
    error: {
      fondo: "var(--error-fondo)",
      borde: "var(--error-borde)",
      texto: "var(--error)",
    },
    info: {
      fondo: "var(--acento-suave)",
      borde: "var(--acento-borde)",
      texto: "var(--acento)",
    },
  }[tono];

  return (
    <div
      style={{
        background: mapa.fondo,
        border: `1px solid ${mapa.borde}`,
        borderRadius: 6,
        padding: "0.8rem 1rem",
        fontSize: "0.86rem",
        color: "var(--tinta-media)",
      }}
    >
      {titulo && (
        <strong style={{ color: mapa.texto, display: "block", marginBottom: children ? "0.25rem" : 0 }}>
          {titulo}
        </strong>
      )}
      {children}
    </div>
  );
}

/** Chip de estado para movimientos y ajustes. */
export function Chip({
  tono,
  children,
}: {
  tono: "ok" | "alerta" | "neutro" | "acento";
  children: ReactNode;
}) {
  const mapa = {
    ok: { fondo: "var(--ok-fondo)", texto: "var(--ok)", borde: "var(--ok-borde)" },
    alerta: {
      fondo: "var(--alerta-fondo)",
      texto: "var(--alerta)",
      borde: "var(--alerta-borde)",
    },
    neutro: {
      fondo: "var(--superficie-alt)",
      texto: "var(--tinta-suave)",
      borde: "var(--borde)",
    },
    acento: {
      fondo: "var(--acento-suave)",
      texto: "var(--acento)",
      borde: "var(--acento-borde)",
    },
  }[tono];

  return (
    <span
      style={{
        display: "inline-block",
        padding: "0.1rem 0.4rem",
        borderRadius: 3,
        fontSize: "0.68rem",
        fontWeight: 650,
        letterSpacing: "0.03em",
        textTransform: "uppercase",
        background: mapa.fondo,
        color: mapa.texto,
        border: `1px solid ${mapa.borde}`,
        whiteSpace: "nowrap",
      }}
    >
      {children}
    </span>
  );
}
