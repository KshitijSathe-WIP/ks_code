# Create Azure App Service Deployment Package
# Run this from the project root directory

Write-Host "🚀 Creating Azure deployment package..." -ForegroundColor Cyan

# Ensure we're in the right directory
$projectRoot = "C:\Data_KS\OneDrive - Wipro\Project Data\KS_Code\ServiceMgmt\incident-rca-foundry"
Set-Location $projectRoot

# Clean up old deploy.zip if it exists
if (Test-Path "deploy.zip") {
    Remove-Item "deploy.zip" -Force
    Write-Host "🗑️  Removed old deploy.zip" -ForegroundColor Yellow
}

# Copy requirements-azure.txt to requirements.txt (Azure expects requirements.txt)
Copy-Item "requirements-azure.txt" "requirements.txt" -Force
Write-Host "📋 Copied requirements-azure.txt → requirements.txt" -ForegroundColor Green

# Create the ZIP file with necessary files
$filesToZip = @(
    "src",
    "requirements.txt"
)

Write-Host "📦 Compressing files..." -ForegroundColor Cyan
Compress-Archive -Path $filesToZip -DestinationPath "deploy.zip" -Force

# Clean up temp requirements.txt
Remove-Item "requirements.txt" -Force

# Get the file size
$fileSize = (Get-Item "deploy.zip").Length / 1MB
$fileSizeMB = [math]::Round($fileSize, 2)

Write-Host ""
Write-Host "✅ Deployment package created successfully!" -ForegroundColor Green
Write-Host "   📦 File: deploy.zip" -ForegroundColor White
Write-Host "   📊 Size: $fileSizeMB MB" -ForegroundColor White
Write-Host ""
Write-Host "Next steps:" -ForegroundColor Cyan
Write-Host "1. Go to Azure Portal: https://portal.azure.com" -ForegroundColor White
Write-Host "2. Navigate to your App Service" -ForegroundColor White
Write-Host "3. Deployment Center → Upload deploy.zip" -ForegroundColor White
Write-Host "   OR" -ForegroundColor Yellow
Write-Host "   Go to: https://[your-app-name].scm.azurewebsites.net/ZipDeployUI" -ForegroundColor White
Write-Host "   Drag and drop deploy.zip" -ForegroundColor White
Write-Host ""
Write-Host "📖 Full instructions: docs\azure-appservice-manual-deployment.md" -ForegroundColor Gray
