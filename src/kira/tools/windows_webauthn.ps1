$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
[Console]::InputEncoding = New-Object System.Text.UTF8Encoding($false)
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
$kiraCompileTemp = Join-Path ([IO.Path]::GetTempPath()) ('kira-webauthn-' + [Guid]::NewGuid().ToString('N'))
$kiraPreviousTemp = $env:TEMP
$kiraPreviousTmp = $env:TMP
try {
    [IO.Directory]::CreateDirectory($kiraCompileTemp) | Out-Null
    $env:TEMP = $kiraCompileTemp
    $env:TMP = $kiraCompileTemp
    $kiraSourceLine = [Console]::In.ReadLine()
    if ($null -eq $kiraSourceLine -or $kiraSourceLine.Length -gt 262144) { throw 'Invalid source' }
    $kiraSource = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($kiraSourceLine))
    Add-Type -TypeDefinition $kiraSource -ReferencedAssemblies System.Web.Extensions,System.Windows.Forms,System.Drawing
    [KiraWebAuthn]::Run()
} catch {
    [Console]::Out.WriteLine('{"type":"availability","ok":false,"apiVersion":0,"platformAuthenticatorAvailable":false}')
} finally {
    $env:TEMP = $kiraPreviousTemp
    $env:TMP = $kiraPreviousTmp
    if ([IO.Directory]::Exists($kiraCompileTemp)) {
        Remove-Item -LiteralPath $kiraCompileTemp -Recurse -Force -ErrorAction SilentlyContinue
    }
}
