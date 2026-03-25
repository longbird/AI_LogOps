import os, sys, json, traceback

os.chdir(r"D:\Work\AI_Projects\AI-LogOps")
sys.path.insert(0, r"D:\Work\AI_Projects\AI-LogOps")

result_file = r"D:\Work\AI_Projects\AI-LogOps\analysis_result.json"
error_file = r"D:\Work\AI_Projects\AI-LogOps\analysis_error.txt"

try:
    from pathlib import Path
    from server.analysis.log_analyzer import LogAnalyzer

    log_path = Path(r"D:\Work\AI_Projects\AI-LogOps\storage\logs\PC-DAERIGO\20260324")
    files = sorted(log_path.glob('*.txt'))

    analyzer = LogAnalyzer('PC-DAERIGO', '20260324')
    result = analyzer.analyze_files(files)

    output = {
        'total_lines': result.total_lines,
        'analysis_period': list(result.analysis_period),
        'version_segments': [{'version': s.version, 'build_info': s.build_info, 'start_time': s.start_time, 'end_time': s.end_time, 'line_start': s.line_start, 'line_end': s.line_end} for s in result.version_segments],
        'inbound_count': result.inbound_count,
        'outbound_count': result.outbound_count,
        'trunk_outbound_count': result.trunk_outbound_count,
        'smdr_call_count': result.smdr_call_count,
        'file_close_count': result.file_close_count,
        'db_fail_count': result.db_fail_count,
        'db_fail_causes_summary': {},
        'outbound_reset_count': result.outbound_reset_count,
        'duration_mismatch_count': result.duration_mismatch_count,
        'session_invalidated_count': result.session_invalidated_count,
        'sspp_loss_count': result.sspp_loss_count,
        'norm_failure_count': result.norm_failure_count,
        'crash_count': result.crash_count,
        'unrecorded_count': len(result.unrecorded_calls),
        'unrecorded_reasons': {},
        'dir_filter_dist': {str(k): v for k, v in result.dir_filter_dist.items()},
        'hourly_dist': result.hourly_dist,
        'norm_failure_details': [{'ts': n.timestamp, 'orig': n.original, 'norm': n.normalized, 'candidates': n.candidates, 'recovered': n.recovered} for n in result.norm_failure_details[:20]],
        'duration_mismatches': [{'ts': d.timestamp, 'ext': d.ext, 'smdr': d.smdr_duration, 'rec': d.rec_duration, 'diff': d.diff, 'cause': d.cause} for d in result.duration_mismatches[:20]],
        'unrecorded_calls': [{'ts': u.timestamp, 'ext': u.ext, 'caller': u.caller, 'called': u.called, 'duration': u.duration, 'reason': u.reason} for u in result.unrecorded_calls[:30]],
    }

    for c in result.db_fail_causes:
        output['db_fail_causes_summary'][c.cause] = output['db_fail_causes_summary'].get(c.cause, 0) + 1

    for u in result.unrecorded_calls:
        output['unrecorded_reasons'][u.reason] = output['unrecorded_reasons'].get(u.reason, 0) + 1

    with open(result_file, 'w', encoding='utf-8') as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

except Exception as e:
    with open(error_file, 'w', encoding='utf-8') as f:
        f.write(traceback.format_exc())
