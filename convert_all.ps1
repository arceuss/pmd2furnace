# PMD to Furnace Batch Converter
# Converts all .M files in PC-98 Audio Rips to Furnace modules

$ErrorActionPreference = "Continue"
$InputDir = "PC-98 Audio Rips"
$OutputDir = "Furnace Output"

Write-Host "============================================" -ForegroundColor Cyan
Write-Host "PMD to Furnace Batch Converter" -ForegroundColor Cyan
Write-Host "============================================" -ForegroundColor Cyan
Write-Host ""

# Create main output directory
if (-not (Test-Path $OutputDir)) {
    New-Item -ItemType Directory -Path $OutputDir | Out-Null
}

# Get all game folders
$GameFolders = Get-ChildItem -Path $InputDir -Directory

$TotalFiles = 0
$SuccessCount = 0
$FailCount = 0

foreach ($GameFolder in $GameFolders) {
    $GameName = $GameFolder.Name
    Write-Host ""
    Write-Host "Processing: $GameName" -ForegroundColor Yellow
    Write-Host "----------------------------------------"
    
    # Create output folder for this game
    $GameOutputDir = Join-Path $OutputDir $GameName
    if (-not (Test-Path $GameOutputDir)) {
        New-Item -ItemType Directory -Path $GameOutputDir | Out-Null
    }
    
    # Get all .M files in the game folder
    $MFiles = Get-ChildItem -Path $GameFolder.FullName -Filter "*.M"
    
    foreach ($MFile in $MFiles) {
        $TotalFiles++
        $OutputFile = Join-Path $GameOutputDir ($MFile.BaseName + ".fur")
        
        Write-Host "  Converting: $($MFile.Name)" -NoNewline
        
        # Run conversion
        & python pmd2furnace.py "$($MFile.FullName)" "$OutputFile" 2>&1 | Out-Null
        
        if (Test-Path $OutputFile) {
            Write-Host " [OK]" -ForegroundColor Green
            $SuccessCount++
        } else {
            Write-Host " [FAILED]" -ForegroundColor Red
            $FailCount++
        }
    }
}

Write-Host ""
Write-Host "============================================" -ForegroundColor Cyan
Write-Host "Conversion complete!" -ForegroundColor Cyan
Write-Host "  Total files: $TotalFiles"
Write-Host "  Success: $SuccessCount" -ForegroundColor Green
if ($FailCount -gt 0) {
    Write-Host "  Failed: $FailCount" -ForegroundColor Red
}
Write-Host "  Output saved to: $OutputDir"
Write-Host "============================================" -ForegroundColor Cyan
