# scripts/make_ca_bundle.ps1
# Exports the Windows trusted-CA stores to config/windows-ca-bundle.pem so that
# Python's curl_cffi/requests can verify TLS on machines where antivirus or a
# network middlebox intercepts HTTPS with a locally-trusted certificate.
# engine/data.py picks the file up automatically on Windows if it exists.
# The file is machine-specific and gitignored — rerun this script on a new PC.

$pem = Join-Path (Split-Path $PSScriptRoot -Parent) "config\windows-ca-bundle.pem"
$sb = New-Object System.Text.StringBuilder

# Both Root (trusted roots) and CA (intermediates) stores, machine + user scope.
foreach ($store in @("Cert:\LocalMachine\Root", "Cert:\CurrentUser\Root",
                     "Cert:\LocalMachine\CA",   "Cert:\CurrentUser\CA")) {
    Get-ChildItem $store -ErrorAction SilentlyContinue | ForEach-Object {
        $null = $sb.AppendLine("-----BEGIN CERTIFICATE-----")
        $null = $sb.AppendLine([Convert]::ToBase64String($_.RawData, "InsertLineBreaks"))
        $null = $sb.AppendLine("-----END CERTIFICATE-----")
    }
}

[IO.File]::WriteAllText($pem, $sb.ToString())
Write-Output "Wrote $((Get-Item $pem).Length) bytes to $pem"
