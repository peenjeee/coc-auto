# CoC LDPlayer farming

Bot farming Clash of Clans untuk LDPlayer (Python + ADB + OpenCV). Desa asal: filter minimum loot, pasukan ditahan sampai habis, Warden + ability, spell di jalur pasukan, hero, siege. Desa malam: deploy menyebar, dua stage, klaim Gerobak Eliksir. Mode `--until-full` menyerang sampai gudang kedua desa penuh.

**Bot melanggar aturan Supercell; akun dapat diblokir. Tidak ada anti-ban.** Tidak ada pembelian, gems, upgrade, login otomatis, modifikasi game atau bypass.

## Status

Diuji pada satu instance LDPlayer 1280×720 (bahasa Indonesia): kedua desa, pindah desa, popup bonus bintang, Gerobak Eliksir, dan baca kapasitas gudang. Belum terbukti: satu sesi `--until-full` utuh sampai berhenti sendiri. Koordinat dan template **tidak** disertakan (`config.json` dan `calibration/` diabaikan Git); setiap pengguna wajib kalibrasi sendiri. Update UI game, army berbeda, atau resolusi lain membatalkan kalibrasi.

## Persiapan Windows

1. Python 3.10+; `python -m pip install -r requirements.txt`. OpenCV dengan GUI, bukan headless.
2. Opsional: Tesseract hanya cadangan bila template angka (`digits`) belum dibuat. [Panduan instalasi](https://tesseract-ocr.github.io/tessdoc/Installation.html).
3. Buka LDPlayer/CoC manual, aktifkan local ADB. Pertahankan resolusi, zoom, bahasa dan UI scale.
4. Periksa serial perangkat di PowerShell:

```powershell
& "C:\LDPlayer\LDPlayer14\adb.exe" devices
```

Gunakan serial yang benar-benar muncul berstatus `device`; jangan menebak port/serial. Ganti path bila instalasi berbeda.

## Config

Ganti serial contoh dengan hasil `adb devices`; angka filter contoh, sesuaikan sendiri:

```powershell
python calibrate.py --init --serial emulator-5554 --gold 500000 --elixir 500000 --dark 3000
```

`--init` menolak overwrite. Field: `adb`, `serial`, `tesseract`, `resolution`, `minimum`, `screens`. Resolusi awal 1280×720; ubah sebelum kalibrasi bila berbeda. Minimum `0` menonaktifkan resource tersebut; semua minimum aktif harus terpenuhi. Ubah `config.json` hanya saat bot berhenti. Config/executable harus terpercaya; inspeksi dahulu bila berasal dari unduhan.

## Kalibrasi

Tampilkan layar terkait secara manual. Klik/drag pada **jendela screenshot**, bukan game. Kalibrasi hanya capture, tidak menekan tombol emulator.

```powershell
python calibrate.py --screen home
python calibrate.py --screen home_menu
python calibrate.py --screen home_army
python calibrate.py --screen home_scout --counts 40 40
python calibrate.py --screen home_battle
python calibrate.py --screen home_result
python calibrate.py --screen builder
python calibrate.py --screen builder_menu
python calibrate.py --screen builder_scout --counts 6
python calibrate.py --screen builder_battle
python calibrate.py --screen builder_stage2 --counts 2
python calibrate.py --screen builder_result
```

- `home`/`builder`: desa idle; pilih tombol `attack`, titik perahu `switch`.
  Anchor boleh punya `margin` (piksel, maks 40) untuk UI yang bergeser sedikit, misalnya bar atas desa malam.
- `zoom_out`: pinch dua jari di layar desa sebelum pindah desa/menyerang, supaya zoom battle (termasuk stage 2 desa malam) sama dengan kalibrasi. `selection` menghentikan bot jika bangunan tetap terpilih.
- `touch_device` (mis. `/dev/input/event4`): tap deploy dikirim sebagai event sentuh langsung, satu panggilan shell per kartu.
- Kartu pasukan: `until_empty` (tap per 4 sampai kartu abu-abu), `points` (titik bergiliran, mis. spell di jalur pasukan), `wait` (detik sebelum kartu, maks 20).
- `capacity`: per desa, angka di bar emas/elixir dibandingkan dengan "Maks" dari tooltip (bar ditekan sekali untuk membuka, sekali lagi untuk menutup; dibaca sekali per sesi, elixir dulu baru emas). `storage` (warna bar) hanya cadangan. `python bot.py --until-full --live` mulai dari desa malam, menyerang desa yang belum penuh, pindah desa saat angka ≥ Maks, dan berhenti saat kedua desa penuh. Tidak ada batas jumlah serangan; hentikan dengan Ctrl+C. `--searches 0` (default) = cari lawan tanpa batas.
- Popup bonus bintang (`star_bonus`, `builder_star_bonus`) ditutup otomatis lewat Oke; popup lain menghentikan bot.
- Desa malam: sebelum menyerang, bot membuka Gerobak Eliksir (`targets.cart`, geser peta dulu), tekan Ambil, lalu tutup (`builder_cart`). Gerobak tidak terlihat = dilewati tanpa tap. Dengan `--until-full`, desa malam baru selesai jika gudang emas/elixir penuh dan teks gerobak `isi / maks` (`cart_text`) juga penuh.
- `digits`: template angka loot (buat ulang dengan `python calibrate.py --learn-digits shot.png:EMAS:ELIXIR ...`).
- `targets.switch`: posisi perahu ikut kamera, jadi bot mencari ikon (`{"file": "<ikon PNG>", "max_score": 0.12}`, opsional `pan`, `scales`, `avoid`) dan berhenti jika tidak ditemukan atau ambigu.
- `hold` (detik): jari ditahan di titik deploy sampai kartu abu-abu. `tap_only`: tekan kartu saja, mis. ability hero.
- `*_menu`: menu matchmaking, tombol `find`.
- `*_scout`: lawan sebelum deploy. `home_scout`: tombol Next dan crop **angka saja** untuk tiap resource, tanpa ikon/label.
- `*_battle`: sesudah deploy mulai. `builder_stage2`: tahap berikutnya siap deploy; pilih anchor yang tetap terlihat setelah deploy.
- `*_result`: hasil dengan tombol `return`.
- Dua anchor UI statis unik, tidak overlap; jangan timer, loot, animasi, jumlah pasukan, bangunan. Pilih bagian yang berubah ketika popup muncul. Dua anchor tidak menjamin setiap popup terdeteksi.
- `--counts 40 40`: dua slot, masing-masing 40 tap deploy; bukan housing space. Urutan kartu, ability hero dan titik spell diatur lewat `troops` di `config.json`.
- Satu titik deploy harus di luar garis merah. Legal pada satu lawan belum tentu legal untuk semua base. Jangan pilih shop/gems/UI lain.
- Profil existing memerlukan `--replace`; config disimpan atomik, template memakai nama baru.
- Bila loading menyebabkan stop, kalibrasi `--screen loading` dengan anchor loading unik. Layar asing tetap menghentikan bot.
- Jika versi game tidak memiliki menu/tahap tersebut, alur perlu disesuaikan setelah screenshot diperiksa. Jangan memaksakan dua state dengan anchor identik.

Screenshot lokal opsional, capture menolak overwrite:

```powershell
python calibrate.py --capture home.png
python calibrate.py --screen home --image home.png
```

Tidak ada screenshot dikirim ke layanan OCR/AI. Simpan di `captures/` bila ingin diabaikan Git.

## Dry-run

```powershell
python bot.py --mode home
python bot.py --mode builder
python bot.py --mode both
```

Tanpa `--live`, **tidak ada tap/swipe**. Memeriksa layar saat ini saja; pada `home_scout` membaca loot dan melaporkan filter. Tidak menavigasi atau mensimulasikan battle. Periksa tiap layar manual, OCR beberapa lawan dan popup.

## Live opt-in

Setelah kalibrasi/dry-run, mulai dari desa idle. Perintah ini benar-benar mengendalikan akun, dapat menghabiskan biaya pencarian/resource dan memengaruhi trophy. Jalankan hanya jika menerima risikonya:

```powershell
python bot.py --mode both --cycles 1 --live
python bot.py --until-full --live
```

`both --cycles 1`: satu serangan tiap desa (cycles 1–100). `--searches 0` (default) mencari lawan tanpa batas; angka lain membatasi jumlah pencarian. Ctrl+C berhenti; input yang sudah dikirim tidak bisa dibatalkan. Battle dapat memerlukan penyelesaian manual.

OCR kosong/confidence rendah, layar asing/ambigu, disconnect, resolusi berubah, timeout menghentikan bot. Tidak ada recovery tap sembarang. Tunggu hasil, tidak surrender otomatis. Tidak menjamin kemenangan atau seluruh unit berhasil dideploy.

## Tes

```powershell
python -m unittest discover -v
python bot.py --help
python calibrate.py --help
```

Tes screenshot sintetis/perangkat palsu: parser OCR, filter, no-shell/serial, dry-run, template/popup, timeout, pergantian lawan, dua desa/tahap. Bukan bukti kompatibilitas game nyata. Coverage belum diukur; pytest/coverage/linter tidak dipasang otomatis.

## Referensi

[MyBot](https://github.com/MyBotRun/MyBot), [CoC_Bot](https://github.com/m24842/CoC_Bot), [CLI LDPlayer](https://www.ldplayer.net/blog/introduction-to-ldplayer-command-line-interface.html). Referensi pendekatan saja, kode tidak disalin. Status/lisensi/kompatibilitas repo belum diverifikasi ulang independen; tools ini bukan fork/instalasi bot tersebut.
