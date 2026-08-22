# Photo signer (`sismo-fotos-signer`)

Presigns S3 uploads for the ATC-20 field form's photos. The browser cannot hold
AWS credentials, so the form posts the inspector's Firebase ID token here and
gets back a short-lived (5 min) presigned `PUT` URL.

Deployed as its own Vercel project — **not** as part of the dashboard project —
because it is called cross-origin by the field form
(`https://formulario-atc20-cali.vercel.app`).

| | |
|---|---|
| Endpoint | `https://sismo-fotos-signer.vercel.app/api/sign` |
| Vercel project | `sismo-fotos-signer` |
| Caller | `formulario/js/form.js` (`FOTO_SIGNER_URL`) |
| Object key | `evaluaciones/{codigo}/foto_{slot}.jpg` |

## Why this directory exists

This service used to live only inside its Vercel deployment, with the AWS keys
and the Firebase API key hardcoded in the source. That made it invisible to the
repository: when the project migrated from the `dagma-85aad` Firebase project to
`sismo-agosto-sgred`, the signer kept validating tokens against the old project
and every photo upload failed with `401 invalid-token`, with nothing in the repo
to point at the cause.

The source now lives here, and every value that can change lives in environment
variables. Rotating AWS keys, pointing at a different Firebase project, or
raising the photo cap is a settings change plus a redeploy — never a code edit.

## Environment variables

Set in Vercel → Project Settings → Environment Variables.

| Variable | Purpose |
|---|---|
| `SIGNER_S3_BUCKET` | Target bucket for uploaded photos |
| `SIGNER_S3_REGION` | Bucket region (default `us-east-1`) |
| `SIGNER_FIREBASE_API_KEY` | Web API key of the Firebase project issuing inspector ID tokens (public identifier, not a secret) |
| `SIGNER_MAX_SLOT` | Highest photo slot accepted |
| `SIGNER_AWS_ACCESS_KEY_ID` | IAM credentials with `PutObject` on the bucket (sensitive) |
| `SIGNER_AWS_SECRET_ACCESS_KEY` | (sensitive) |

A request against a deployment missing any required variable returns
`500 {"error":"config","missing":[...]}` instead of failing later with an opaque
AWS error.

## Keeping the photo cap in sync

`SIGNER_MAX_SLOT` must be **greater than or equal to** `MAX_FOTOS` in
`formulario/js/logic.js`. The form refuses to add slots past its own
`MAX_FOTOS`; the signer rejects any slot above `SIGNER_MAX_SLOT` with
`400 bad-request`. If the signer's ceiling is the lower of the two, uploads fail
only for the extra photos, and only at submit time — raise the signer first,
then the form.

## Deploying

The project is not connected to git, so a push here does not redeploy it.
Deploy the contents of this directory explicitly:

```bash
cd services/photo-signer
npx vercel link --project sismo-fotos-signer   # first time only
npx vercel deploy --prod
```

## Verifying after a deploy

Sign-only check (no upload). Mint an ID token from the current Firebase project
and request a signature for the highest slot you expect to support:

```bash
curl -s -X POST https://sismo-fotos-signer.vercel.app/api/sign \
  -H "Content-Type: application/json" \
  -d '{"idToken":"<firebase-id-token>","codigo":"76001-1-0040001","slot":10}'
```

| Response | Meaning |
|---|---|
| `200` with `uploadUrl` | Working |
| `401 invalid-token` | Token is from a different Firebase project than `SIGNER_FIREBASE_API_KEY` |
| `400 bad-request` | Slot above `SIGNER_MAX_SLOT`, or malformed `codigo` |
| `500 config` | A required environment variable is missing |
