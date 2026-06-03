/**
 * MondayBrief — weekly email wrapper around the PDF attachment.
 *
 * Render with: `npx react-email export --dir . --outDir ./out`
 * The Python `send/postmark.py` reads the exported HTML and ships it as the
 * email body alongside the PDF attachment.
 *
 * react-email: https://github.com/resend/react-email (MIT)
 */
import * as React from "react";
import {
  Body,
  Button,
  Container,
  Head,
  Heading,
  Hr,
  Html,
  Preview,
  Section,
  Text,
} from "@react-email/components";

type Lead = {
  name: string;
  score: number;
  margin_est_monthly: number;
  category: string;
  drive_minutes?: number;
};

type Props = {
  clientName: string;
  weekOf: string;
  leads: Lead[];
  pdfUrl: string;
};

export default function MondayBrief({
  clientName = "E&K Commercial Cleaning",
  weekOf = "May 25, 2026",
  leads = [
    { name: "Lincoln Park Dental Studio", score: 92, margin_est_monthly: 1840, category: "dental clinic", drive_minutes: 8 },
    { name: "West Loop Vet Clinic", score: 89, margin_est_monthly: 2140, category: "vet clinic", drive_minutes: 9 },
    { name: "Halsted Coffee Roasters", score: 87, margin_est_monthly: 1240, category: "cafe", drive_minutes: 11 },
  ],
  pdfUrl = "https://mondaybrief.app/brief/2026-05-25.pdf",
}: Props) {
  const top = leads[0];
  const totalMargin = leads.reduce((s, l) => s + l.margin_est_monthly, 0);

  return (
    <Html lang="en">
      <Head />
      <Preview>{leads.length} new leads inside your service area this week — top: {top?.name}</Preview>
      <Body style={body}>
        <Container style={container}>
          <Heading style={h1}>MondayBrief — {clientName}</Heading>
          <Text style={metaStrip}>Week of {weekOf}</Text>

          <Section style={summaryCard}>
            <Text style={summaryText}>
              {leads.length} new leads inside your route radius. Top pick scored {top?.score} —
              that's <strong>${top?.margin_est_monthly.toLocaleString()}/mo</strong> if you win it.
              Projected combined margin across all {leads.length}: <strong>${totalMargin.toLocaleString()}/mo</strong>.
            </Text>
          </Section>

          <Hr style={hr} />

          {leads.map((lead, i) => (
            <Section key={i} style={leadRow}>
              <Text style={leadName}>
                {i + 1}. {lead.name}{" "}
                <span style={{ color: "#0a7d57", fontWeight: 700 }}>· score {lead.score}</span>
              </Text>
              <Text style={leadMeta}>
                {lead.category}
                {lead.drive_minutes != null ? ` · +${lead.drive_minutes} min off route` : ""}
                {" · "}~${lead.margin_est_monthly.toLocaleString()}/mo
              </Text>
            </Section>
          ))}

          <Hr style={hr} />

          <Section style={{ textAlign: "center", margin: "24px 0" }}>
            <Button href={pdfUrl} style={cta}>
              Open full PDF brief
            </Button>
          </Section>

          <Text style={footerText}>
            Every lead links back to the official City of Chicago record. Reply STOP to pause briefs.
          </Text>
          <Text style={footerAttribution}>
            Map data © OpenStreetMap contributors (ODbL). Business filings © City of Chicago Open Data.
          </Text>
        </Container>
      </Body>
    </Html>
  );
}

const body: React.CSSProperties = {
  backgroundColor: "#f5f6f8",
  fontFamily: "ui-sans-serif, system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif",
  margin: 0,
  padding: "24px 0",
};
const container: React.CSSProperties = {
  margin: "0 auto",
  padding: "28px 32px",
  maxWidth: "560px",
  backgroundColor: "#ffffff",
  borderRadius: "10px",
  border: "1px solid #e6e8ec",
};
const h1: React.CSSProperties = { fontSize: "20px", fontWeight: 700, color: "#1a1a1a", margin: "0 0 4px" };
const metaStrip: React.CSSProperties = { color: "#666", fontSize: "13px", margin: "0 0 16px" };
const summaryCard: React.CSSProperties = {
  backgroundColor: "#f6f8fa",
  borderLeft: "3px solid #0a7d57",
  padding: "12px 14px",
  borderRadius: "4px",
};
const summaryText: React.CSSProperties = { fontSize: "14px", color: "#2a2a2a", margin: 0, lineHeight: "1.55" };
const hr: React.CSSProperties = { borderTop: "1px solid #e6e8ec", margin: "18px 0" };
const leadRow: React.CSSProperties = { margin: "10px 0" };
const leadName: React.CSSProperties = { fontSize: "14.5px", fontWeight: 600, color: "#1a1a1a", margin: "0 0 2px" };
const leadMeta: React.CSSProperties = { fontSize: "12.5px", color: "#666", margin: 0 };
const cta: React.CSSProperties = {
  backgroundColor: "#0a7d57",
  color: "#ffffff",
  padding: "11px 22px",
  borderRadius: "8px",
  fontSize: "14px",
  fontWeight: 600,
  textDecoration: "none",
  display: "inline-block",
};
const footerText: React.CSSProperties = { fontSize: "11.5px", color: "#888", margin: "8px 0", textAlign: "center" };
const footerAttribution: React.CSSProperties = { fontSize: "10px", color: "#aaa", margin: "4px 0 0", textAlign: "center" };
