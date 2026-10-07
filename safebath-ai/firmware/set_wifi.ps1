<#
.SYNOPSIS
  Point every SafeBath AI ESP32 node at your network in one command.

.DESCRIPTION
  Rewrites only these three lines in every firmware/*/*.ino:

      const char* ssid        = "...";
      const char* password    = "...";
      const char* mqtt_server = "...";

  Values come from -Ssid/-Password/-MqttServer, or from wifi_credentials.local
  (git-ignored) when no parameters are given. Alignment and trailing comments on
  those lines are preserved; UTF-8 (no BOM) and LF endings are preserved.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File firmware\set_wifi.ps1
  powershell -ExecutionPolicy Bypass -File firmware\set_wifi.ps1 `
              -Ssid 'MyHomeWiFi' -Password 'changeme' -MqttServer 192.168.1.20
#>
param(
    [string]$Ssid,
    [string]$Password,
    [string]$MqttServer,
    [string]$Config = (Join-Path $PSScriptRoot 'wifi_credentials.local')
)

$ErrorActionPreference = 'Stop'
$utf8NoBom = New-Object System.Text.UTF8Encoding($false)

if (-not ($Ssid -and $Password -and $MqttServer)) {
    if (-not (Test-Path -LiteralPath $Config)) {
        throw "No credentials given and $Config not found. Copy the template, edit it, or pass -Ssid/-Password/-MqttServer."
    }
    foreach ($line in (Get-Content -LiteralPath $Config -Encoding UTF8)) {
        if ($line -match '^\s*ssid\s*=\s*(.*?)\s*$')        { $Ssid       = $Matches[1] }
        elseif ($line -match '^\s*password\s*=\s*(.*?)\s*$'){ $Password   = $Matches[1] }
        elseif ($line -match '^\s*mqtt_server\s*=\s*(.*?)\s*$') { $MqttServer = $Matches[1] }
    }
    if (-not ($Ssid -and $Password -and $MqttServer)) {
        throw "$Config is missing one of: ssid, password, mqtt_server"
    }
    Write-Host "config: $Config"
}

$targets = Get-ChildItem -LiteralPath $PSScriptRoot -Recurse -Filter *.ino -File
foreach ($f in $targets) {
    $text = [System.IO.File]::ReadAllText($f.FullName, $utf8NoBom)
    $before = $text
    $fields = @{ 'ssid' = $Ssid; 'password' = $Password; 'mqtt_server' = $MqttServer }
    foreach ($name in $fields.Keys) {
        $value = $fields[$name]
        # ${1}/${2} (brace form) so a digit-leading value can't be read as a
        # multi-digit group reference — "$1" directly before "42" would parse
        # as a single group number and swallow both groups
        $safe = $value -replace '([$\\])', '\$1'
        $pattern = "(?m)^([ \t]*const char\*[ \t]*$name[ \t]*=[ \t]*`")[^`"]*(`";.*)$"
        $text = [regex]::Replace($text, $pattern, '${1}' + $safe + '${2}')
    }
    if ($text -ne $before) {
        [System.IO.File]::WriteAllText($f.FullName, $text, $utf8NoBom)
        Write-Host ("  updated  {0}" -f $f.Name)
    } else {
        Write-Host ("  unchanged {0}" -f $f.Name)
    }
}
Write-Host ("done - {0} sketch(es) point at ssid '{1}' / broker {2}" -f $targets.Count, $Ssid, $MqttServer)
