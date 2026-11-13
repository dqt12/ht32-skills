<#
HT32 工程上下文模板结构校验（验证"模板可被脚本消费"契约）：
- 模板版本标记存在
- 标题为 # HT32_PROJECT.md — {{project_name}}
- 九个固定 H2 section 存在（标题文本精确匹配）
- 枚举全部 {{field}} 占位符（脚本填充契约）
- AGENTS.md 薄引导标记唯一、有序、引用 HT32_PROJECT.md

用法：powershell -ExecutionPolicy Bypass -File validate_template.ps1 [-Template <模板路径>] [-Guide <引导路径>]
退出码：0 通过；1 失败。
#>
[CmdletBinding()]
param(
    [string]$Template = '',
    [string]$Guide = ''
)
$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
if (-not $Template) { $Template = Join-Path $repoRoot 'templates\HT32_PROJECT.template.md' }
if (-not $Guide) { $Guide = Join-Path $repoRoot 'templates\AGENTS.guide.md' }
if (-not (Test-Path $Template)) { Write-Host "FAIL: 模板不存在：$Template"; exit 1 }

$content = Get-Content $Template -Raw -Encoding UTF8
$fail = @()

$templateVersion = ''
if ($content -match 'ht32-project:template-version:(\d+)') {
    $templateVersion = $Matches[1]
} else {
    $fail += '缺少模板版本标记 ht32-project:template-version:N'
}

if ($content -notmatch '(?m)^# HT32_PROJECT\.md — \{\{project_name\}\}\r?$') {
    $fail += '缺少标题行 # HT32_PROJECT.md — {{project_name}}'
}

$requiredSections = @(
    '## 1. 项目概述',
    '## 2. 芯片选型核对结论',
    '## 3. 外设与资源清单',
    '## 4. 使用的库与例程路径',
    '## 5. 需求记录',
    '## 6. 变更记录',
    '## 7. 工具链与版本记录',
    '## 8. 代码规范与约定',
    '## 9. 本次执行计划与进度'
)
foreach ($s in $requiredSections) {
    if ($content -notmatch ('(?m)^' + [regex]::Escape($s) + '\r?$')) { $fail += "缺少 section：$s" }
}

$placeholders = @([regex]::Matches($content, '\{\{[a-z_]+\}\}') | ForEach-Object { $_.Value } | Sort-Object -Unique)
$requiredDebuggerFields = @('{{debugger_backend}}', '{{debugger_probe_serial}}', '{{debugger_interface}}', '{{debugger_speed_khz}}')
foreach ($field in $requiredDebuggerFields) {
    if ($placeholders -cnotcontains $field) { $fail += "缺少 debugger 占位符：$field" }
}
if ($placeholders -ccontains '{{debugger_serial}}') { $fail += '拒绝旧占位符：{{debugger_serial}}' }

if (-not (Test-Path $Guide)) {
    $fail += "缺少 AGENTS.md 薄引导模板：$Guide"
} else {
    $guideContent = Get-Content $Guide -Raw -Encoding UTF8
    $startMarker = '<!-- ht32-project-guide:start -->'
    $endMarker = '<!-- ht32-project-guide:end -->'
    $startCount = ([regex]::Matches($guideContent, [regex]::Escape($startMarker))).Count
    $endCount = ([regex]::Matches($guideContent, [regex]::Escape($endMarker))).Count
    if ($startCount -ne 1) { $fail += "薄引导起始标记必须恰好一个（实际 $startCount）" }
    if ($endCount -ne 1) { $fail += "薄引导结束标记必须恰好一个（实际 $endCount）" }
    if ($startCount -eq 1 -and $endCount -eq 1 -and $guideContent.IndexOf($startMarker) -gt $guideContent.IndexOf($endMarker)) {
        $fail += '薄引导标记顺序错误：end 早于 start'
    }
    if ($guideContent -notmatch 'HT32_PROJECT\.md') { $fail += '薄引导未引用 HT32_PROJECT.md' }
    if ($guideContent -match '\{\{') { $fail += '薄引导不得包含 {{...}} 占位符' }
}

if ($fail.Count -gt 0) {
    $fail | ForEach-Object { Write-Host "FAIL: $_" }
    exit 1
}
Write-Host ('OK: 模板结构有效（{0}）' -f $Template)
Write-Host ("模板版本：{0}" -f $templateVersion)
Write-Host ("占位符（{0} 个）：{1}" -f $placeholders.Count, ($placeholders -join ', '))
Write-Host ('薄引导模板有效（{0}）' -f $Guide)
exit 0
