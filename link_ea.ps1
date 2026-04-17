#$Source = "C:\Users\QuangXAU\projects\kog-strategy\ZoneSignalEA"
#$Dest = "C:\Users\QuangXAU\AppData\Roaming\MetaQuotes\Terminal\7D70CC401B91FAC031C1DD6731E80E7A\MQL5\Experts\ZoneSignalEA"

$Source = "C:\Users\QuangXAU\projects\kog-strategy-data"
$Dest = "C:\Users\QuangXAU\AppData\Roaming\MetaQuotes\Terminal\7D70CC401B91FAC031C1DD6731E80E7A\MQL5\Files\ZoneSignalEA"

# 1. Cleanup: Remove the destination if it already exists
if (Test-Path $Dest) {
    # If it's a directory, we use Remove-Item. 
    # If it's an old link, it will still be removed correctly.
    Remove-Item -Path $Dest -Recurse -Force
}

# 2. Create the Link using the CMD wrapper
# /D creates a Directory Symbolic Link
# Note the quotes around the variables to handle spaces in paths
cmd /c mklink /D "$Dest" "$Source"