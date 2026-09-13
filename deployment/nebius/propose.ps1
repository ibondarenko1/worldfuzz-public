[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][string]$PythonPath,
    [Parameter(Mandatory=$true)][string]$RequestPath,
    [Parameter(Mandatory=$true)][string]$AuthorizationPath,
    [Parameter(Mandatory=$true)][string]$OutputDirectory
)
# Future explicitly approved inference only; no key creation or publication.
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$previousKey = $env:NEBIUS_API_KEY
$pointer = [IntPtr]::Zero
$secureKey = $null
try {
    $keyPath = Join-Path $env:LOCALAPPDATA 'WorldFuzz\nebius-api-key.dpapi'
    $item = Get-Item -LiteralPath $keyPath -Force
    if ($item.Length -gt 65536 -or ($item.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
        throw 'Credential path must be a small regular DPAPI file.'
    }
    $secureKey = ConvertTo-SecureString -String ([IO.File]::ReadAllText($keyPath))
    $pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secureKey)
    $env:NEBIUS_API_KEY = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer)
    $scriptPath = Join-Path $PSScriptRoot 'competition.py'
    & $PythonPath $scriptPath propose --request $RequestPath --authorization $AuthorizationPath --out $OutputDirectory
    if ($LASTEXITCODE -ne 0) { throw 'Provider attempt stopped. Inspect the private receipt; do not retry.' }
}
finally {
    $env:NEBIUS_API_KEY = $previousKey
    if ($pointer -ne [IntPtr]::Zero) { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer) }
    if ($null -ne $secureKey) { $secureKey.Dispose() }
}
