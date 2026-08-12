# Kontrak Temuan (domain glossary)

Kosakata yang dipakai bersama lintas repo (`sentinel-agent-server` sumber
kebenaran, `sentinel-backend`, `sentinel`). Tiap istilah di sini adalah
**seam** tempat interface antarservis hidup — jangan menduplikasinya dengan
nama lain di repo konsumen.

## Temuan (Finding)

Satu pelanggaran, satu baris per transaksi acuan, bersifat permanen. Dipecah
tiga muatan karena pembaca dan umurnya berbeda:

- `description` — narasi LLM, kalimat pertama yang dibaca orang keuangan.
- `evidence` — fakta Python (trigger, baseline, skor); tetap sah walau LLM gagal.
- `analysis` — cara AI menyimpulkan (verdict 3 agen, penyesuaian semantik);
  nullable saat LLM mati.

Status **open** = `resolution IS NULL`; **resolved** = `resolution` terisi.
`status` pada GET `/api/findings` adalah **filter query**, bukan field.

## Resolution (alasan menutup temuan)

Bukan boolean — *kenapa* ditutup lebih penting daripada *bahwa* ditutup.

`justified` · `false_positive` · `confirmed_fraud` · `escalated`

## Risk level (kode, bukan label)

Kode masuk database dan di-constraint. Label adalah tampilan dan boleh berubah.

`low` · `medium` · `high` · `critical`

## Analisis per transaksi

`clean` (diperiksa, bersih) · `flagged` (punya temuan) · `failed` (gagal) —
membedakan "belum pernah diperiksa" dari "sudah diperiksa dan bersih".

## Kontrak lintas-repo

Satu-satunya sumber: `openapi/snapshot.json` (agent server), dihasilkan
`scripts/export_openapi.py`. Konsumen men-generate:
- backend: `scripts/contract-pull.mjs` → `src/contract/findings.contract.js`
- frontend: `scripts/contract-pull.mjs` → `lib/contract/findings.contract.ts`

Ubah enum → jalankan `export_openapi.py` + `contract:pull` di tiap repo.
`contract:check` menandai yang tertinggal. Lihat ADR-0001.
