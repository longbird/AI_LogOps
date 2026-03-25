# AirRec Log Analysis GUI - Technical Specification

## Architecture

### New Files
1. `server/analysis/log_analyzer.py` - Batch log analysis engine
2. `server/analysis/report_generator.py` - Markdown report generator
3. `server/gui/tabs/log_analysis_tab.py` - Tkinter GUI tab

### Modified Files
4. `server/gui/app.py` - Register new tab (2 lines)

## Design Decisions

- **Download**: Use existing `HIST_REQUEST` flow (`POST /api/logs/{agent_id}/history`)
- **Analysis module**: `server/analysis/` (alongside existing `monitor_state.py`)
- **Log encoding**: CP949 with UTF-8 fallback (AirRec is C++ with Korean locale)
- **Processing**: Line-by-line streaming (not loading full file into memory)
- **Threading**: Background thread for download/analysis, `root.after()` for UI updates

## Component Details

### 1. LogAnalyzer (server/analysis/log_analyzer.py)
- Input: List of log file paths
- Output: AnalysisResult dataclass
- Patterns to detect:
  - `AirRecorder v` / `InitInstance` → version segments
  - `I:IN_END` / `O:OUT_END` → inbound/outbound counts
  - `DB_UPDATE FAIL` → failure count + cause classification
  - `OUTBOUND-RESET` → direction mismatch reset count
  - `DirFilter` → direction filter distribution (0/1/2)
  - `DURATION-MISMATCH` → mismatch count
  - `Session invalidated` / `Aborted.*session` → is_used guard triggers
  - `Idx:-1` → SSPP table loss
  - `no match.*candidates` → normalization failure
  - `O:OUT_END.*Dnis:850[1-9]` → trunk outbound count
- Version segmentation: Split analysis by InitInstance boundaries
- Hourly distribution: Count events per hour (0-23)

### 2. ReportGenerator (server/analysis/report_generator.py)
- Input: AnalysisResult
- Output: Markdown string
- Sections:
  1. Overview (agent, date, version timeline)
  2. Core metrics table
  3. DB_UPDATE FAIL cause classification
  4. Direction filter distribution
  5. Hourly distribution (text table)
  6. Issues/warnings

### 3. LogAnalysisTab (server/gui/tabs/log_analysis_tab.py)
- Top section: Agent dropdown, date entry (YYYYMMDD), folder dropdown, download button
- Middle section: Analyze button, progress label
- Bottom section: ScrolledText for report display, save button
- Flow: Select agent → Download logs → Analyze → View report → Save
