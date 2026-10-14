import { ImageResponse } from "next/og";

export const ogSize = { width: 1200, height: 630 };

/** Brand share card: coal background, ember glow, title, optional price line. */
export function ogCard(title: string, sub: string, tag = "PAKISTAN'S FIRST READY-TO-GRILL BBQ BOX") {
  return new ImageResponse(
    (
      <div
        style={{
          width: "100%",
          height: "100%",
          display: "flex",
          flexDirection: "column",
          justifyContent: "space-between",
          padding: 72,
          background: "radial-gradient(circle at 80% 85%, rgba(242,101,42,0.55), rgba(12,13,15,1) 55%)",
          backgroundColor: "#0c0d0f",
          color: "#f6f5f3",
          fontFamily: "sans-serif",
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: 18 }}>
          <div style={{ width: 56, height: 56, borderRadius: 999, background: "#f2652a", display: "flex", alignItems: "center", justifyContent: "center", color: "#0c0d0f", fontSize: 26, fontWeight: 800 }}>
            IG
          </div>
          <div style={{ fontSize: 34, fontWeight: 700 }}>Instant Grill</div>
        </div>
        <div style={{ display: "flex", flexDirection: "column", gap: 18 }}>
          <div style={{ fontSize: 22, letterSpacing: 4, color: "#ffa47a", fontWeight: 700 }}>{tag}</div>
          <div style={{ fontSize: 76, fontWeight: 800, lineHeight: 1.02, letterSpacing: -2, maxWidth: 950 }}>{title}</div>
          <div style={{ fontSize: 32, color: "#b9bbc1" }}>{sub}</div>
        </div>
      </div>
    ),
    ogSize,
  );
}
