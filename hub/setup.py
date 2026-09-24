"""Initialize an empty or synthetic demonstration library without network/model calls."""
import argparse
import json
from .store import ROOT, DATA, connect, upsert, review, now
from .pipeline import RunLock

def initialize(demo=False):
    with RunLock():
        db=connect()
        try:
            if demo:
                if db.execute('SELECT count(*) FROM papers').fetchone()[0] or any((ROOT/'knowledge/wiki').rglob('*.md')):
                    raise ValueError('Demo requires an empty library. Use a separate checkout; existing work is never overwritten.')
                payload=json.loads((ROOT/'examples/demo.json').read_text(encoding='utf-8'))
                for item in payload['papers']:
                    paper={**item['paper'],'demo':True,'sources':['synthetic-demo']}
                    upsert(db,paper)
                    review(db,paper['id'],{**item['review'],'reviewed_at':now(),
                           'summary_status':'synthetic_demo','read_level':'synthetic_demo'})
                (DATA/'seed.json').write_text(json.dumps({'opportunities':payload['opportunities']},ensure_ascii=False,indent=2),encoding='utf-8')
            from .export import export
            export(db)
        finally:db.close()

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--demo',action='store_true');a=p.parse_args()
    initialize(a.demo)
    print('Synthetic demo ready (not research evidence).' if a.demo else 'Local library initialized.')
    print('Run: python -m hub.server --port 8767')

if __name__=='__main__':main()
