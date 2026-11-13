<#
HT32 本地工具链检测脚本（ht32-env-check 配套）
只读检测 Keil5(UV4) / HT32 DFP / CMSIS-DAP / 串口(COM) / clang-format，不修改系统任何配置。
Windows PowerShell 5.1 兼容，仅用系统自带能力。

用法：
  powershell -ExecutionPolicy Bypass -File check_local_tools.ps1 [-Uv4Path <路径>] [-Json]
#>
[CmdletBinding()]
param(
    [string]$Uv4Path = '',
    [switch]$Json
)

$ErrorActionPreference = 'Stop'

# 输出编码：JSON 里的中文统一按 UTF-8 写出（宿主/agent 按 UTF-8 读取不会乱码）
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch { }

function Find-Uv4 {
    param([string]$Hint)
    if ($Hint) {
        if (Test-Path $Hint) { return @{ found = $true; path = (Resolve-Path $Hint).Path; source = 'explicit' } }
        return @{ found = $false; path = $Hint; source = 'explicit-not-found' }
    }
    # 全局数据目录可用 HT32_SKILLS_HOME 重定向（宿主沙箱）；与 runtime 的解析口径一致
    $dataRoot = if ($env:HT32_SKILLS_HOME) { $env:HT32_SKILLS_HOME } else { "$env:USERPROFILE\.ht32-skills" }
    foreach ($cfg in @("$dataRoot\config.json")) {
        if (Test-Path $cfg) {
            try {
                $keilCfg = Get-Content $cfg -Raw -Encoding UTF8 | ConvertFrom-Json
                if ($keilCfg.tools.uv4_exe -and (Test-Path $keilCfg.tools.uv4_exe -PathType Leaf)) {
                    return @{ found = $true; path = $keilCfg.tools.uv4_exe; source = 'machine-config' }
                }
            } catch { }
        }
    }
    foreach ($p in @('C:\Keil_v5\UV4\UV4.exe', 'D:\Keil_v5\UV4\UV4.exe', 'C:\Keil\UV4\UV4.exe')) {
        if (Test-Path $p) { return @{ found = $true; path = $p; source = 'common-path' } }
    }
    foreach ($rk in @('HKLM:\SOFTWARE\WOW6432Node\Keil\Products\MDK', 'HKLM:\SOFTWARE\Keil\Products\MDK')) {
        if (Test-Path $rk) {
            $installDir = (Get-ItemProperty $rk -ErrorAction SilentlyContinue).Path
            if ($installDir) {
                $exe = Join-Path $installDir 'UV4\UV4.exe'
                if (Test-Path $exe) { return @{ found = $true; path = $exe; source = 'registry' } }
            }
        }
    }
    $cmd = Get-Command 'UV4.exe' -ErrorAction SilentlyContinue
    if ($cmd) { return @{ found = $true; path = $cmd.Source; source = 'PATH' } }
    return @{ found = $false; path = $null; source = $null }
}

function Find-Ht32Dfp {
    param([string]$KeilRoot)
    $roots = @()
    if ($KeilRoot) { $roots += (Join-Path $KeilRoot 'ARM\PACK') }
    $roots += (Join-Path $env:LOCALAPPDATA 'Arm\Packs')
    $packs = @()
    foreach ($r in $roots) {
        if (Test-Path $r) {
            Get-ChildItem $r -Directory -ErrorAction SilentlyContinue |
                Where-Object { $_.Name -like 'Holtek*' } |
                ForEach-Object { $packs += $_.FullName }
        }
    }
    $packs = @($packs | Select-Object -Unique)
    return @{ found = ($packs.Count -gt 0); packs = $packs }
}

function Get-UsbDevicesFromRegistry {
    # CIM / Get-PnpDevice 被宿主沙箱或权限拦住时的回退：直接读 Enum\USB。
    # 注意：注册表保留历史设备记录，可能含当前未插入的探针——结果必须标注 probe_source。
    $root = 'HKLM:\SYSTEM\CurrentControlSet\Enum\USB'
    if (-not (Test-Path $root)) { return @() }
    $devices = @()
    try {
        foreach ($vidKey in Get-ChildItem $root -ErrorAction Stop) {
            foreach ($instance in Get-ChildItem $vidKey.PSPath -ErrorAction SilentlyContinue) {
                $props = Get-ItemProperty $instance.PSPath -ErrorAction SilentlyContinue
                $desc = [string]$props.DeviceDesc
                if (-not $desc) { $desc = [string]$instance.PSChildName }
                $devices += [pscustomobject]@{
                    Name        = $desc
                    Description = $desc
                    PNPDeviceID = ('USB\{0}\{1}' -f $vidKey.PSChildName, $instance.PSChildName)
                }
            }
        }
    } catch { }
    return $devices
}

function Find-CmsisDap {
    $probeSource = 'cim'
    $probeLimited = $false
    $devices = @()
    try {
        $usbDevices = @(Get-CimInstance Win32_PnPEntity -Filter "PNPDeviceID LIKE 'USB%'" -ErrorAction Stop)
    } catch {
        $usbDevices = @()
        $probeLimited = $true
    }
    if ($probeLimited) {
        # CIM 被拦时回退注册表（与 runtime 的 Python 探测同口径）；CIM 正常返回空则尊重空结果
        $usbDevices = @(Get-UsbDevicesFromRegistry)
        if ($usbDevices.Count -gt 0) { $probeSource = 'registry' }
    }
    foreach ($dev in $usbDevices) {
        $hay = ('{0} {1}' -f $dev.Name, $dev.Description)
        # 名称/描述类标识：CMSIS-DAP / DAPLink / ULINK / e-Link 等
        $nameMatch = $hay -match '(?i)cmsis.?dap|daplink|dap-?link|ulink|e-?link'
        # 厂商 VID 类标识：0D28(DAPLink) / C251(Keil) / 1241(Atmel) ，
        # 以及合泰自家调试器 VID_04D9&PID_802F(802F) / 807C(e-LinkPro2)——
        # 合泰 VID 04D9 键鼠也用，故必须带 PID，否则会误判
        $vidMatch = $dev.PNPDeviceID -match 'VID_(0D28|C251|1241)|VID_04D9&PID_(802F|807C)'
        if ($nameMatch -or $vidMatch) {
            $by = 'usb-vid'
            if ($nameMatch) { $by = 'name' }
            $devices += @{ name = $dev.Name; instance = $dev.PNPDeviceID; matched_by = $by }
        }
    }
    return @{ found = ($devices.Count -gt 0); devices = $devices; probe_source = $probeSource; probe_limited = $probeLimited }
}

function Get-ComPorts {
    $ports = @()
    try { $ports = @([System.IO.Ports.SerialPort]::GetPortNames()) } catch { }
    # 友好名优先注册表（Unicode 原生，不受控制台代码页影响），CIM 只补缺
    $map = @{}
    try {
        Get-ChildItem 'HKLM:\SYSTEM\CurrentControlSet\Enum\USB' -Recurse -ErrorAction Stop | ForEach-Object {
            $paramsKey = Join-Path $_.PSPath 'Device Parameters'
            if (Test-Path $paramsKey) {
                $port = (Get-ItemProperty $paramsKey -ErrorAction SilentlyContinue).PortName
                if ($port -and $port -match '^COM\d+$') {
                    $friendly = (Get-ItemProperty $_.PSPath -ErrorAction SilentlyContinue).FriendlyName
                    if ($friendly) { $map[$port.ToUpper()] = $friendly }
                }
            }
        }
    } catch { }
    $wmi = @()
    if ($ports.Count -gt 0 -and $map.Count -lt $ports.Count) {
        try { $wmi = @(Get-CimInstance Win32_PnPEntity -Filter "Name LIKE '%(COM%'" -ErrorAction Stop) } catch { }
    }
    $mapped = @()
    foreach ($p in $ports) {
        $friendly = $map[$p.ToUpper()]
        if (-not $friendly) {
            foreach ($w in $wmi) {
                if ($w.Name -match "\($p\)") { $friendly = $w.Name; break }
            }
        }
        $mapped += @{ port = $p; name = $friendly }
    }
    return @{ count = $mapped.Count; ports = $mapped }
}

function Find-ClangFormat {
    $cmd = Get-Command 'clang-format.exe' -ErrorAction SilentlyContinue
    $candidates = @()
    if ($cmd) { $candidates += $cmd.Source }
    foreach ($base in @($env:ProgramFiles, ${env:ProgramFiles(x86)})) {
        if ($base) { $candidates += (Join-Path $base 'LLVM\bin\clang-format.exe') }
    }
    foreach ($path in $candidates) {
        if ($path -and (Test-Path $path -PathType Leaf)) {
            $version = ''
            try { $version = ((& $path --version 2>$null) -join ' ').Trim() } catch { }
            return @{ found = $true; path = $path; version = $version }
        }
    }
    return @{ found = $false; path = $null; version = $null }
}

$keil = Find-Uv4 -Hint $Uv4Path

$keilRoot = $null
if ($keil.found) { $keilRoot = Split-Path (Split-Path $keil.path) }

$dfp = Find-Ht32Dfp -KeilRoot $keilRoot
$dap = Find-CmsisDap
$serial = Get-ComPorts
$clangFormat = Find-ClangFormat

$warnings = @()
if (-not $keil.found) { $warnings += '未找到 Keil5 UV4.exe：无法基线编译/烧录（安装 Keil MDK v5，或在全局 .ht32-skills/config.json 的 tools.uv4_exe 配置 UV4 路径）' }
if (-not $dfp.found) { $warnings += '未找到 HT32 DFP pack：编译将找不到器件（Keil Pack Installer 安装 Holtek HT32 DFP）' }
if (-not $dap.found -and -not $dap.probe_limited) { $warnings += '未检测到 CMSIS-DAP 调试器：无法烧录/在线调试（接板或检查 HID/WinUSB 驱动）' }
if ($dap.probe_limited -and $dap.probe_source -eq 'registry') { $warnings += 'USB 探测受限（CIM 不可用，已回退注册表）：CMSIS-DAP 结果可能含历史设备——若实际未接探针，以 Keil Debug 设置 / 实际烧录为准' }
if ($dap.probe_limited -and $dap.probe_source -ne 'registry') { $warnings += 'USB 探测受限（CIM 不可用，注册表也无记录）：CMSIS-DAP 状态未知，不要当作"没接调试器"' }
if ($serial.count -eq 0) { $warnings += '未发现串口（COM）设备：无法查看目标板日志（检查 USB 转串口驱动）' }
if (-not $clangFormat.found) { $warnings += '未找到 clang-format：写码后无法做格式校验（装 LLVM 或把它加入 PATH）；不阻断，交付时须说明"未做格式校验"' }

$keilState = 'missing'
if ($keil.found) { $keilState = 'ok' }
$dfpState = 'missing'
if ($dfp.found) { $dfpState = 'ok' }
$dapState = 'missing'
if ($dap.found) { $dapState = 'ok' }
elseif ($dap.probe_limited) { $dapState = 'unknown' }
$clangState = 'missing'
if ($clangFormat.found) { $clangState = 'ok' }

$result = [ordered]@{
    status   = 'ok'
    action   = 'check_local_tools'
    summary  = ("Keil5:{0} DFP:{1} CMSIS-DAP:{2} Serial:{3} clang-format:{4}" -f $keilState, $dfpState, $dapState, $serial.count, $clangState)
    details  = [ordered]@{
        keil         = $keil
        ht32_dfp     = $dfp
        cmsis_dap    = $dap
        serial       = $serial
        clang_format = $clangFormat
    }
    warnings = $warnings
}

if ($Json) {
    $result | ConvertTo-Json -Depth 6
} else {
    Write-Host '==== HT32 本地工具链检测 ===='
    Write-Host ("Keil5 (UV4) : {0}  {1}  [{2}]" -f $keilState, $keil.path, $keil.source)
    Write-Host ("HT32 DFP    : {0}  {1}" -f $dfpState, ($dfp.packs -join '; '))
    Write-Host ("CMSIS-DAP   : {0}  [{1}]" -f $dapState, $dap.probe_source)
    foreach ($d in $dap.devices) { Write-Host ("    - {0}  (matched: {1})" -f $d.name, $d.matched_by) }
    Write-Host ("串口 (COM)  : {0} 个" -f $serial.count)
    foreach ($p in $serial.ports) { Write-Host ("    - {0}  {1}" -f $p.port, $p.name) }
    Write-Host ("clang-format: {0}  {1}" -f $clangState, $clangFormat.version)
    if ($warnings.Count -gt 0) {
        Write-Host ''
        Write-Host '警告：'
        $warnings | ForEach-Object { Write-Host "  - $_" }
    }
}
