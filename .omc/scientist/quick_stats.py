import pickle, os
from collections import defaultdict
from datetime import datetime
with open(".omc/scientist/smdr_state.pkl", "rb") as f: s = pickle.load(f)
ia=s["ia_answers"]; i_end=s["i_in_end"]; o_end=s["o_out_end"]
fop=s["file_opens"]; fcl=s["file_closes"]; nt5=s["no_type5"]
pe=s["pending_expired"]; tl=s["total_lines"]
rc=s["rec_checks"]
fc_gt0=[r for r in fcl if r[4]>0]
skip1=[r for r in rc if r[6]==1]; skip0=[r for r in rc if r[6]==0]
o_gt0=[r for r in o_end if r[3]>0]
i_gt0=[r for r in i_end if r[4]>0]
should=len(ia)+len(o_gt0)
rrate=round(len(fc_gt0)/should*100,2)
print("REPORT_RATE:",rrate)
print("REPORT_SHOULD:",should)
print("REPORT_ACTUAL:",len(fc_gt0))
print("REPORT_IA:",len(ia))
print("REPORT_SKIP1:",len(skip1))
print("REPORT_NO_TYPE5:",len(nt5))
print("REPORT_PE:",len(pe))