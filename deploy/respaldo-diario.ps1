<#
PLANTILLA (NO ACTIVA, no se ejecuta sola). Registra una tarea diaria de Windows para el respaldo cifrado del portal CxC.
Solo usa CLAVES PUBLICAS. Ejecutar manualmente, una sola vez, tras la aprobacion de Alejandro y con rutas REALES revisadas.
    .\respaldo-diario.ps1 -Python <python.exe> -Repo <ruta del repo> -Estado <carpeta estado> -Destino <carpeta de respaldos> -Publicas <a.pub.pem>,<b.pub.pem> [-Hora 02:30] [-Registrar]
Sin -Registrar solo muestra lo que haria (modo de prueba).
#>
param(
  [Parameter(Mandatory)][string]$Python, [Parameter(Mandatory)][string]$Repo, [Parameter(Mandatory)][string]$Estado,
  [Parameter(Mandatory)][string]$Destino, [Parameter(Mandatory)][string[]]$Publicas,
  [string]$Hora = "02:30", [int]$Diarios = 14, [int]$Mensuales = 6, [switch]$Registrar
)
$pub = ($Publicas | ForEach-Object { "--publica `"$_`"" }) -join " "
$args = "-m cxc_portal.admin respaldar-cifrado --estado `"$Estado`" --destino `"$Destino`" $pub --diarios $Diarios --mensuales $Mensuales"
Write-Host "Tarea 'CxC-Portal-Respaldo-Cifrado': $Python $args  (diaria $Hora, directorio $Repo)"
if (-not $Registrar) { Write-Host "Modo de prueba: no se registro nada. Use -Registrar para crearla."; return }
$accion = New-ScheduledTaskAction -Execute $Python -Argument $args -WorkingDirectory $Repo
$disparador = New-ScheduledTaskTrigger -Daily -At $Hora
Register-ScheduledTask -TaskName "CxC-Portal-Respaldo-Cifrado" -Action $accion -Trigger $disparador -Description "Respaldo cifrado diario del Portal CxC (solo claves publicas)" | Out-Null
Write-Host "Tarea registrada."
