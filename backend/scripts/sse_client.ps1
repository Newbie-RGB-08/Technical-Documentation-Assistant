param(
    [Parameter(Mandatory = $true)][string]$Question,
    [string]$SessionId = "",
    [int]$MaxSeconds = 120
)

$body = @{ question = $Question; top_k = 5 } | ConvertTo-Json
if ($SessionId) { $body = @{ question = $Question; session_id = $SessionId; top_k = 5 } | ConvertTo-Json }

$req = [System.Net.HttpWebRequest]::Create("http://127.0.0.1:8000/api/chat/stream")
$req.Method = "POST"
$req.ContentType = "application/json"
$bytes = [System.Text.Encoding]::UTF8.GetBytes($body)
$req.GetRequestStream().Write($bytes, 0, $bytes.Length)

$resp = $req.GetResponse()
$stream = $resp.GetResponseStream()
$reader = New-Object System.IO.StreamReader($stream, [System.Text.Encoding]::UTF8)

$sw = [Diagnostics.Stopwatch]::StartNew()
$buf = ""
$contentText = ""
while (-not $reader.EndOfStream -and $sw.Elapsed.TotalSeconds -lt $MaxSeconds) {
    $line = $reader.ReadLine()
    if ($null -eq $line) { break }
    if ($line -eq "") {
        if ($buf -match "(?s)event: (\w+)\ndata: (.*)") {
            $ev = $Matches[1]; $data = $Matches[2]
            if ($ev -eq "content") {
                $t = ($data | ConvertFrom-Json).text
                $contentText += $t
                Write-Host -NoNewline $t
            } else {
                Write-Host "`n[$ev] $data"
            }
        }
        $buf = ""
    } else {
        $buf += $line + "`n"
    }
}
Write-Host "`n----- 内容总长度: $($contentText.Length) -----"
$reader.Close(); $resp.Close()
