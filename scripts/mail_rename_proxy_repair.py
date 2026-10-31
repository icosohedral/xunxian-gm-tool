"""Prepare the purchase proxy fix for delivery to a renamed character."""
_OLD = '''      c.execute("SELECT char_id FROM common_characters WHERE name=%s",(recipient,));target=c.fetchone()
      target_id=int(target[0]) if target else cid
'''
_NEW = '''      c.execute("SELECT char_id FROM common_characters WHERE name=%s",(recipient,));target=c.fetchone()
      if not target and cid:
       target=(cid,)
      if not target:
       c.execute("SELECT char_id FROM char_used_name WHERE old_name=%s OR new_name=%s LIMIT 1",(recipient,recipient));target=c.fetchone()
      target_id=int(target[0]) if target else cid
'''


def prepare_proxy_fix(source: str) -> str:
    if source.count(_OLD) != 1 or _NEW in source:
        raise ValueError('点券代理源码与已核对版本不一致')
    result = source.replace(_OLD, _NEW, 1)
    compile(result, 'transactions.py', 'exec')
    return result
