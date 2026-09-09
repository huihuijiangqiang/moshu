[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^v\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?$')]
    [string]$Version,

    [string]$Title,
    [string]$NotesPath,
    [switch]$Publish,
    [switch]$Draft,
    [switch]$SkipAsset
)

$ErrorActionPreference = "Stop"

function Invoke-Git {
    param([Parameter(Mandatory = $true)][string[]]$Arguments)
    & git @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "git 命令失败：git $($Arguments -join ' ')"
    }
}

function Get-GitHubToken {
    $token = $env:GITHUB_TOKEN
    if ([string]::IsNullOrWhiteSpace($token)) {
        $token = $env:GH_TOKEN
    }
    if (-not [string]::IsNullOrWhiteSpace($token)) {
        return $token
    }

    $credentialInput = "protocol=https`nhost=github.com`n`n"
    $credential = $credentialInput | git credential fill 2>$null
    return (($credential | Where-Object { $_ -like "password=*" }) -replace '^password=', '')
}

$root = (git rev-parse --show-toplevel).Trim()
if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($root)) {
    throw "当前目录不在 Git 仓库内"
}
Set-Location $root

$remote = (git remote get-url origin).Trim()
if ($LASTEXITCODE -ne 0 -or $remote -notmatch 'github\.com[:/](?<repo>[^/]+/[^/]+?)(?:\.git)?$') {
    throw "origin 不是可识别的 GitHub 仓库地址"
}
$repository = $Matches.repo
$repositoryName = ($repository -split '/')[-1]

$branch = (git branch --show-current).Trim()
if ($branch -ne 'main') {
    throw "发布必须从 main 分支执行，当前分支：$branch"
}

$status = @(git status --porcelain)
if ($status.Count -gt 0) {
    throw "工作区不干净，请先提交改动后再发布"
}

Invoke-Git @('fetch', 'origin', 'main', '--quiet')
$head = (git rev-parse HEAD).Trim()
$remoteHead = (git rev-parse origin/main).Trim()
if ($head -ne $remoteHead) {
    throw "本地 main 与 origin/main 不一致，请先同步后再发布"
}

$existingTag = git rev-parse --verify --quiet "refs/tags/$Version" 2>$null
if ($LASTEXITCODE -eq 0 -and -not [string]::IsNullOrWhiteSpace($existingTag)) {
    throw "版本标签已存在：$Version"
}

if ([string]::IsNullOrWhiteSpace($Title)) {
    $Title = "墨枢 $Version：长篇小说创作工作台"
}

$artifactDirectory = Join-Path $root '.release-artifacts'
New-Item -ItemType Directory -Force -Path $artifactDirectory | Out-Null
$archivePath = Join-Path $artifactDirectory "$repositoryName-$Version-source.zip"
if (Test-Path -LiteralPath $archivePath) {
    Remove-Item -LiteralPath $archivePath -Force
}
Invoke-Git @('archive', '--format=zip', "--prefix=$repositoryName-$Version/", "--output=$archivePath", $head)

if (-not [string]::IsNullOrWhiteSpace($NotesPath)) {
    $resolvedNotesPath = (Resolve-Path -LiteralPath $NotesPath).Path
    $notes = Get-Content -LiteralPath $resolvedNotesPath -Raw
} else {
    $previousTag = (git tag --sort=-version:refname | Select-Object -First 1)
    $range = if ([string]::IsNullOrWhiteSpace($previousTag)) { $head } else { "$previousTag..$head" }
    $commits = @(git log $range --pretty=format:'- %s' -n 30)
    $commitText = if ($commits.Count -gt 0) { $commits -join "`n" } else { '- 首次发布' }
    $notes = @"
## 主要更新

- 长篇小说写作台与持续章纲
- 作品设定库、结构化检索和一致性守卫
- 按模型窗口弹性装配上下文
- 自然化审查与漫剧分镜基础能力
- Docker 部署、账号权限和用量管理

## 本次提交

$commitText

## 部署

请参照 README 和 `server/.env.example` 配置服务，再执行 Docker Compose 启动。
本版本不会携带任何本机环境变量、密钥或数据卷。
"@
}

Write-Output "版本：$Version"
Write-Output "提交：$head"
Write-Output "源码包：$archivePath"

if (-not $Publish) {
    Write-Output "预览完成。确认发布时重新执行并追加 -Publish。"
    exit 0
}

$token = Get-GitHubToken
if ([string]::IsNullOrWhiteSpace($token)) {
    throw "未找到 GitHub 凭据。请设置 GITHUB_TOKEN/GH_TOKEN，或先登录 Git 凭据管理器。"
}
$headers = @{
    Authorization = "Bearer $token"
    Accept = 'application/vnd.github+json'
    'User-Agent' = 'moshu-release-tool'
}

Invoke-Git @('tag', '--annotate', $Version, $head, '--message', $Title)
try {
    Invoke-Git @('push', 'origin', "refs/tags/$Version")
} catch {
    Write-Error "标签已在本地创建，但推送失败：$Version"
    throw
}

$releasePayload = @{
    tag_name = $Version
    target_commitish = $head
    name = $Title
    body = $notes
    draft = [bool]$Draft
    prerelease = $Version.Contains('-')
    generate_release_notes = $false
} | ConvertTo-Json -Depth 5
$release = Invoke-RestMethod -Method Post -Uri "https://api.github.com/repos/$repository/releases" -Headers $headers -Body $releasePayload -ContentType 'application/json'

if (-not $SkipAsset) {
    $assetName = [IO.Path]::GetFileName($archivePath)
    $uploadUrl = $release.upload_url -replace '\{\?name,label\}', "?name=$([uri]::EscapeDataString($assetName))"
    Invoke-RestMethod -Method Post -Uri $uploadUrl -Headers $headers -InFile $archivePath -ContentType 'application/zip' | Out-Null
}

Write-Output "Release 已创建：$($release.html_url)"
