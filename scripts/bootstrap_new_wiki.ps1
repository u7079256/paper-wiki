<#
.SYNOPSIS
  Bootstrap a new LLM-Wiki project from the paper-wiki skill.

.DESCRIPTION
  Creates a fresh wiki project: copies the slash commands, sub-agents and OCR
  scripts from this skill, lays down variant-specific CLAUDE.md / research.md /
  README.md from templates, and namespaces the OCR pipeline so several wiki
  projects can share one GPU server without clobbering each other.

  This script body is intentionally ASCII-only so it runs on any PowerShell
  regardless of console codepage. All Chinese / non-ASCII text lives in the
  .tmpl data files and is read/written with explicit .NET UTF-8 (NOT
  Get-Content/Set-Content, which corrupt UTF-8 on a GBK console -- see docs/GOTCHAS.md).

.PARAMETER NewPath      Absolute path of the new project, e.g. D:\my-wiki
.PARAMETER Topic        kebab-case id; used for raw/<topic>/ and OCR namespace mineru_<ns>_*
.PARAMETER ProjectName  Human-readable name for titles (default = Topic)
.PARAMETER Variant      research | course   (default research)
.PARAMETER SkillRoot    Path to this skill repo (default = parent of this script's dir)

.EXAMPLE
  .\bootstrap_new_wiki.ps1 -NewPath D:\my-wiki -Topic my-topic -ProjectName "My Wiki"
  .\bootstrap_new_wiki.ps1 -NewPath D:\aml -Topic aml -ProjectName "AML" -Variant course
#>
param(
  [Parameter(Mandatory=$true)][string]$NewPath,
  [Parameter(Mandatory=$true)][string]$Topic,
  [string]$ProjectName = $Topic,
  [ValidateSet('research','course')][string]$Variant = 'research',
  [string]$SkillRoot
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

if (-not $SkillRoot) { $SkillRoot = Split-Path -Parent $PSScriptRoot }
$ns   = (($Topic -replace '[^a-zA-Z0-9]+','_').Trim('_')).ToLower()
$date = Get-Date -Format 'yyyy-MM-dd'

# All template/doc I/O goes through explicit UTF-8 (no BOM) to survive GBK consoles.
function ReadUtf8($p)  { [System.IO.File]::ReadAllText($p, [System.Text.UTF8Encoding]::new($false)) }
function WriteUtf8($p,$s){ [System.IO.File]::WriteAllText($p, $s, [System.Text.UTF8Encoding]::new($false)) }

Write-Host "Skill root : $SkillRoot"
Write-Host "New project: $NewPath"
Write-Host "Variant    : $Variant"
Write-Host "OCR ns     : mineru_${ns}_*"
Write-Host ""

# --- directory skeleton (variant-aware) ------------------------------------
$common = @(
  $NewPath, "$NewPath\raw\$Topic", "$NewPath\scripts",
  "$NewPath\.claude\commands", "$NewPath\.claude\agents", "$NewPath\wiki\notes"
)
if ($Variant -eq 'research') { $wikidirs = @('papers','concepts','gaps','experiments') }
else                          { $wikidirs = @('lectures','topics','practice') }

foreach ($d in $common) { New-Item -ItemType Directory -Force -Path $d | Out-Null }
foreach ($d in $wikidirs) {
  $p = "$NewPath\wiki\$d"
  New-Item -ItemType Directory -Force -Path $p | Out-Null
  if (-not (Test-Path "$p\.gitkeep")) { New-Item -ItemType File -Force -Path "$p\.gitkeep" | Out-Null }
}
foreach ($gk in @("$NewPath\raw\$Topic\.gitkeep","$NewPath\wiki\notes\.gitkeep")) {
  if (-not (Test-Path $gk)) { New-Item -ItemType File -Force -Path $gk | Out-Null }
}

# --- copy commands / agents / scripts (variant-aware subset) ----------------
if ($Variant -eq 'research') {
  $cmds   = @('wiki-init','wiki-compile','wiki-search-latest','wiki-critique','wiki-ideate')
  $agents = @('wiki-searcher','wiki-critic','wiki-ideator')
} else {
  # course/exam wikis do not expand outward, so the search/novelty tooling is dropped
  $cmds   = @('wiki-init','wiki-compile','wiki-critique')
  $agents = @('wiki-critic')
}
foreach ($c in $cmds)   { Copy-Item "$SkillRoot\commands\$c.md" "$NewPath\.claude\commands\" -Force }
foreach ($a in $agents) { Copy-Item "$SkillRoot\agents\$a.md"   "$NewPath\.claude\agents\"   -Force }
Copy-Item "$SkillRoot\scripts\mineru_remote_ocr.py" "$NewPath\scripts\" -Force
Copy-Item "$SkillRoot\scripts\mineru_local_ocr.py"  "$NewPath\scripts\" -Force
Copy-Item "$SkillRoot\scripts\extract_pptx.py"      "$NewPath\scripts\" -Force

# --- bake namespace + topic into the OCR scripts (host/user/pass stay in env) -
foreach ($f in @("$NewPath\scripts\mineru_remote_ocr.py","$NewPath\scripts\mineru_local_ocr.py")) {
  $oc = ReadUtf8 $f
  $oc = $oc -replace '__WIKI_NS__', $ns -replace '__WIKI_TOPIC__', $Topic
  WriteUtf8 $f $oc
}

# --- render templates -------------------------------------------------------
function Render($tmpl, $out) {
  $t = ReadUtf8 $tmpl
  $t = $t -replace '\{\{PROJECT_NAME\}\}', $ProjectName `
          -replace '\{\{TOPIC\}\}', $Topic `
          -replace '\{\{NS\}\}', $ns `
          -replace '\{\{DATE\}\}', $date `
          -replace '\{\{NEWPATH\}\}', $NewPath
  WriteUtf8 $out $t
}
Render "$SkillRoot\templates\$Variant\CLAUDE.md.tmpl"   "$NewPath\CLAUDE.md"
Render "$SkillRoot\templates\$Variant\research.md.tmpl" "$NewPath\research.md"
Render "$SkillRoot\templates\$Variant\README.md.tmpl"   "$NewPath\README.md"

Write-Host "Done -> $NewPath"
Write-Host ""
Write-Host "Next steps:"
Write-Host "  1. cd `"$NewPath`""
Write-Host "  2. (optional, for OCR) copy the memory templates from"
Write-Host "       $SkillRoot\templates\memory\*.tmpl"
Write-Host "     into your Claude Code PROJECT memory dir, then fill in your own GPU"
Write-Host "     server host/user/password LOCALLY. The password must NEVER be committed."
Write-Host "  3. Start Claude Code in $NewPath and run:  /wiki-init"
Write-Host "     (it reads the variant from CLAUDE.md and walks you through the rest)"
