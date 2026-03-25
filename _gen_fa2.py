# Generator script
import base64, zlib
# Read the source template and encode it
src = open("D:/Work/AI_Projects/AI-LogOps/_fa2_src.py", "rb").read()
compressed = zlib.compress(src)
encoded = base64.b64encode(compressed).decode("ascii")
init = open("D:/Work/AI_Projects/AI-LogOps/_fa2.py", "r").read()
with open("D:/Work/AI_Projects/AI-LogOps/_fa2_full.py", "w") as f:
    f.write(init)
    f.write("exec(zlib.decompress(base64.b64decode("" + encoded + "")).decode("utf-8"))
")
print("Full script generated")
