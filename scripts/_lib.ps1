# Shared helpers for KOG deploy scripts.
# Dot-source this file at the top of each deploy script:
#   . "$PSScriptRoot\_lib.ps1"

function Get-LocalTerminals {
    <#
    .SYNOPSIS
        Returns a hashtable of terminal-name -> terminal-object, filtered to the
        terminals that belong to the specified VPS.
    .PARAMETER Deploy
        Parsed deploy.json PSCustomObject.
    .PARAMETER Vps
        VPS label to filter by (e.g. "vps-sg"). Defaults to $env:GH_RUNNER_VPS.
        If empty/null, ALL terminals are returned (manual-run scenario).
    .NOTES
        Backward-compat: a terminal without a "vps" field defaults to "vps-sg".
    #>
    param(
        [Parameter(Mandatory)]
        [PSCustomObject]$Deploy,

        [string]$Vps = $env:GH_RUNNER_VPS
    )

    $result = @{}
    foreach ($prop in $Deploy.terminals.PSObject.Properties) {
        $term = $prop.Value
        if ($term.vps) {
            $termVps = $term.vps
        } else {
            $termVps = 'vps-sg'
        }
        if (-not $Vps -or $termVps -eq $Vps) {
            $result[$prop.Name] = $term
        }
    }
    return $result
}
