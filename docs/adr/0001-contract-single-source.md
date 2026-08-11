# ADR-0001: Kontrak temuan bersumber tunggal lewat OpenAPI

- **Status:** Diterima
- **Tanggal:** 2026-08-12

## Konteks

Kontrak temuan (enum `resolutions`, `risk_levels`, filter `status`, dan bentuk
baris temuan) sebelumnya disalin tangan di tiga repo:

1. `app/core/constants.py` di agent server (sumber kebenaran de facto),
2. `sentinel-backend/src/constants/findings.constant.js` + `finding.validation.js`,
3. `sentinel/lib/services/api.ts` di frontend.

Menambah nilai enum berarti mengedit tiga berkas di dua repo terpisah. Drift
diam-diam merusak alur resolve: nilai baru lolos validasi di satu sisi dan
ditolak di sisi lain. Sudah ada komentar di `findings.constant.js` yang mengakui
"harus berubah bersama", tetapi tidak ada mekanisme yang memaksanya.

`finding.service.js` di backend juga murni meneruskan panggilan ke
`AgentClient` — interface-nya selebar implementasinya (shallow), tanpa logika
domain yang membenarkan lapisan tambahan itu.

## Keputusan

1. **Agent server menjadi sumber kebenaran kontrak.** `app/api_models.py`
   mendefinisikan model Pydantic (`FindingRow`, `FindingSummary`) dengan enum
   `Literal` yang mengimpor nilai dari `app/core/constants.py`, dan endpoint
   temuan memakai `response_model` — sehingga `/openapi.json` memuat enum dan
   bentuk baris yang sebenarnya.

2. **Konsumen men-generate kontrak dari snapshot OpenAPI.** Tiap repo konsumen
   punya `scripts/contract-pull.mjs` yang menarik `openapi/snapshot.json`
   (dihasilkan `scripts/export_openapi.py`) dan menulis berkas contract:
   - backend → `src/contract/findings.contract.js` (dipakai validasi zod),
   - frontend → `lib/contract/findings.contract.ts` (tipe + bentuk baris).
   Fingerprint SHA-256 dari sumber tersimpan di berkas hasil; `contract:check`
   gagal bila tertinggal.

3. **`finding.service.js` dihapus.** Controller memanggil `AgentClient`
   langsung; `AgentClient` adalah satu-satunya adapter ke agent server.

## Konsekuensi

- Nilai enum ditulis sekali (Python); perubahan membutuhkan
  `python scripts/export_openapi.py` lalu `npm run contract:pull` di tiap
  repo konsumen. `contract:check` menandai yang lupa.
- Berkas hasil generate TIDAK boleh diedit tangan; regenerasi menimpanya.
- Perubahan perilaku agent server: `resolve` dengan `resolution` tak dikenal
  kini 422 (validasi FastAPI) menggantikan 200 + `{error}`; `status` query
  tidak dikenal kini 422. Backend sudah memvalidasi di lapisan zod, jadi tak
  ada dampak lewat jalur normal.
