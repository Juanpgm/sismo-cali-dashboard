// Presigns S3 photo uploads for the ATC-20 field form.
//
// All configuration comes from environment variables (Vercel project settings),
// so rotating AWS keys or pointing the form at a different Firebase project is
// a settings change plus a redeploy — never a code edit:
//
//   SIGNER_S3_BUCKET             target bucket for the uploaded photos
//   SIGNER_S3_REGION             bucket region
//   SIGNER_FIREBASE_API_KEY      web API key of the Firebase project that
//                                issues the inspectors' ID tokens (public)
//   SIGNER_MAX_SLOT              highest photo slot accepted (must be >= the
//                                form's MAX_FOTOS in formulario/js/logic.js)
//   SIGNER_AWS_ACCESS_KEY_ID     IAM credentials with PutObject on the bucket
//   SIGNER_AWS_SECRET_ACCESS_KEY (sensitive)
import { S3Client, PutObjectCommand } from "@aws-sdk/client-s3";
import { getSignedUrl } from "@aws-sdk/s3-request-presigner";

const BUCKET = process.env.SIGNER_S3_BUCKET;
const REGION = process.env.SIGNER_S3_REGION || "us-east-1";
const FIREBASE_API_KEY = process.env.SIGNER_FIREBASE_API_KEY;
const MAX_SLOT = Number(process.env.SIGNER_MAX_SLOT || 10);
const ACCESS_KEY_ID = process.env.SIGNER_AWS_ACCESS_KEY_ID;
const SECRET_ACCESS_KEY = process.env.SIGNER_AWS_SECRET_ACCESS_KEY;
const CODIGO_RE = /^76001-[123]-\d{7,8}$/;

const s3 = new S3Client({
  region: REGION,
  credentials: { accessKeyId: ACCESS_KEY_ID, secretAccessKey: SECRET_ACCESS_KEY },
});

function cors(res) {
  res.setHeader("Access-Control-Allow-Origin", "*");
  res.setHeader("Access-Control-Allow-Methods", "POST, OPTIONS");
  res.setHeader("Access-Control-Allow-Headers", "Content-Type");
}

export default async function handler(req, res) {
  cors(res);
  if (req.method === "OPTIONS") return res.status(204).end();
  if (req.method !== "POST") return res.status(405).json({ error: "method" });

  // Fail loudly on a misconfigured deployment instead of presigning against
  // undefined credentials and surfacing a confusing AWS error downstream.
  const missing = [
    ["SIGNER_S3_BUCKET", BUCKET],
    ["SIGNER_FIREBASE_API_KEY", FIREBASE_API_KEY],
    ["SIGNER_AWS_ACCESS_KEY_ID", ACCESS_KEY_ID],
    ["SIGNER_AWS_SECRET_ACCESS_KEY", SECRET_ACCESS_KEY],
  ].filter(([, v]) => !v).map(([k]) => k);
  if (missing.length) {
    return res.status(500).json({ error: "config", missing });
  }

  const { idToken, codigo, slot } = req.body || {};
  const n = Number(slot);
  if (!idToken || !CODIGO_RE.test(codigo || "") || !(n >= 1 && n <= MAX_SLOT)) {
    return res.status(400).json({ error: "bad-request" });
  }

  // Validate the Firebase idToken against Google itself (no crypto deps).
  const r = await fetch(
    `https://identitytoolkit.googleapis.com/v1/accounts:lookup?key=${FIREBASE_API_KEY}`,
    { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ idToken }) },
  );
  const who = r.ok ? await r.json() : null;
  if (!who || !who.users || !who.users.length) {
    return res.status(401).json({ error: "invalid-token" });
  }

  const key = `evaluaciones/${codigo}/foto_${n}.jpg`;
  const uploadUrl = await getSignedUrl(
    s3,
    new PutObjectCommand({ Bucket: BUCKET, Key: key, ContentType: "image/jpeg" }),
    { expiresIn: 300 },
  );
  return res.status(200).json({
    uploadUrl,
    publicUrl: `https://${BUCKET}.s3.amazonaws.com/${key}`,
  });
}
