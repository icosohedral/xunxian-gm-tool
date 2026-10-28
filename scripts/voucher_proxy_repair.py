"""Prepare a narrowly scoped fix for the managed server's point proxy."""

# These fields identify the native item reward requests observed for effect 98.
_ORIGINAL = '''      else:
       after=before-amount;success=1
    reply=bytes([6])+payload[1:5]+struct.pack("<Bi",success,after)
'''
_PATCHED = '''      else:
       after=before-amount;success=1
     elif (ext & 0xffff)==0x60 and payload[-2:]==bytes([0x43,0]):
      # Effect-98 item rewards are negative point requests, not purchase refunds.
      after=before-amount;success=1
    reply=bytes([6])+payload[1:5]+struct.pack("<Bi",success,after)
'''


def prepare_proxy_fix(source: str) -> str:
    """Patch only the known transaction implementation; reject unfamiliar versions."""
    if source.count(_ORIGINAL) != 1 or _PATCHED in source:
        raise ValueError('点券代理源码与已核对版本不一致')
    updated = source.replace(_ORIGINAL, _PATCHED, 1)
    compile(updated, 'transactions.py', 'exec')
    return updated
