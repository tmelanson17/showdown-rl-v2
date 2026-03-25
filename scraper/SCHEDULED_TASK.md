# Pokemon Showdown Replay Scraper - Scheduled Task

## Task Configuration

- **Task Name**: `PokemonShowdownReplayScraper`
- **Schedule**: Every 15 minutes
- **Min ELO Filter**: 1400
- **Output Directory**: `C:\Users\tmela\development\pokemans\showdown-rl-v2\scraper\gen3ou_replays`
- **Format**: gen3ou

## Management Commands

### Check Task Status
```powershell
Get-ScheduledTask -TaskName "PokemonShowdownReplayScraper" | Get-ScheduledTaskInfo
```

### Start Task Immediately
```powershell
Start-ScheduledTask -TaskName "PokemonShowdownReplayScraper"
```

### Stop Task
```powershell
Stop-ScheduledTask -TaskName "PokemonShowdownReplayScraper"
```

### Disable Task
```powershell
Disable-ScheduledTask -TaskName "PokemonShowdownReplayScraper"
```

### Enable Task
```powershell
Enable-ScheduledTask -TaskName "PokemonShowdownReplayScraper"
```

### Remove Task
```powershell
Unregister-ScheduledTask -TaskName "PokemonShowdownReplayScraper" -Confirm:$false
```

## Recreate Task

If you need to recreate the scheduled task:

```powershell
$pythonPath = "C:\Users\tmela\AppData\Local\Microsoft\WindowsApps\python.exe"

$action = New-ScheduledTaskAction -Execute $pythonPath -Argument "C:\Users\tmela\development\pokemans\showdown-rl-v2\scraper\web_scraper.py --format gen3ou --min-elo 1400 --once --output C:\Users\tmela\development\pokemans\showdown-rl-v2\scraper\gen3ou_replays" -WorkingDirectory "C:\Users\tmela\development\pokemans\showdown-rl-v2\scraper"

$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date) -RepetitionInterval (New-TimeSpan -Minutes 15) -RepetitionDuration (New-TimeSpan -Days 365)

$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable

Register-ScheduledTask -TaskName "PokemonShowdownReplayScraper" -Action $action -Trigger $trigger -Settings $settings -Description "Scrapes Pokemon Showdown replays every 15 minutes"
```

## Manual Execution

To run the scraper manually (one-time):

```powershell
cd C:\Users\tmela\development\pokemans\showdown-rl-v2\scraper
python web_scraper.py --format gen3ou --min-elo 1400 --once --output gen3ou_replays
```

To run self-tests:

```powershell
python web_scraper.py --test
```
