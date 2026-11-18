# Off-device copy of the Pi's newest bookings backup (S8). Runs on the desktop.
# The backupreader key can ONLY stream the latest snapshot (forced command).
#   powershell -ExecutionPolicy Bypass -File scripts\pull_pi_backup.ps1
param(
  [string]$Dest = "$env:USERPROFILE\pi-backups",
  [string]$PiHost = "backupreader@100.92.161.12",
  [string]$Key = "$env:USERPROFILE\.ssh\pi_backup_ed25519",
  [int]$Keep = 30
)
$ErrorActionPreference = 'Stop'
New-Item -ItemType Directory -Force $Dest | Out-Null
$stage = Join-Path $env:TEMP ("pi-backup-" + [guid]::NewGuid())
New-Item -ItemType Directory -Force $stage | Out-Null
try {
  $tar = Join-Path $stage 'latest.tar'
  # cmd's redirection is byte-exact; Windows PowerShell 5.1's '>' re-encodes binary.
  cmd /c "ssh -i `"$Key`" -o BatchMode=yes -o IdentitiesOnly=yes -o ConnectTimeout=20 $PiHost > `"$tar`""
  if ($LASTEXITCODE -ne 0) { throw "ssh export failed ($LASTEXITCODE)" }
  tar -xf $tar -C $stage
  if ($LASTEXITCODE -ne 0) { throw "tar extract failed" }
  $db = Get-ChildItem $stage -Filter 'bookings-*.sqlite' | Select-Object -First 1
  if (-not $db) { throw "no snapshot in export" }
  $side = Join-Path $stage ($db.BaseName + '.sha256')
  $want = ((Get-Content $side -Raw).Trim() -split '\s+')[0].ToLower()
  $got = (Get-FileHash $db.FullName -Algorithm SHA256).Hash.ToLower()
  if ($want -ne $got) { throw "checksum mismatch: $($db.Name)" }
  Copy-Item $db.FullName, $side $Dest -Force
  Get-ChildItem $Dest -Filter 'bookings-*.sqlite' | Sort-Object Name -Descending | Select-Object -Skip $Keep | ForEach-Object {
    Remove-Item $_.FullName, ($_.FullName -replace '\.sqlite$', '.sha256') -Force -ErrorAction SilentlyContinue
  }
  "PULL_OK $($db.Name) sha256=$got -> $Dest"
} finally {
  Remove-Item $stage -Recurse -Force -ErrorAction SilentlyContinue
}
