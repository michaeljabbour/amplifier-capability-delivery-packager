import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import struct
import tempfile
import unittest
from unittest.mock import patch
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import delivery_packager as dp

def plan():
    return {'schema_version':'delivery-plan/v1','title':'Fictional repair-club handoff','owner':'Fictional curator',
      'approval_reference':'Fictional instruction approving only the listed files and local destination',
      'destination':{'label':'Fictional coordinator','status':'not_sent','evidence_ref':None},
      'next_action':'Review the draft; no distribution has occurred',
      'unresolved_limits':['No audience acceptance or accessibility outcome measured'],
      'assets':[{'path':'draft.txt','role':'editable','credits':['Original fictional text'],'license':'MIT declaration; ownership not independently verified','accessibility':'Plain text; no affected-user study'},
                {'path':'final/read me.txt','role':'final','credits':['Original fictional text'],'license':'MIT declaration','accessibility':'Plain text'}]}

class DeliveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.base=Path(self.tmp.name);self.source=self.base/'source';self.dest=self.base/'out'
        self.source.mkdir();self.dest.mkdir();(self.source/'final').mkdir()
        (self.source/'draft.txt').write_bytes(b'An editable fictional draft.\n');(self.source/'final/read me.txt').write_bytes(b'Final for review, not published.\n')
        (self.source/'not-approved.txt').write_text('Unselected private working material.')
        self.plan=plan()
    def tearDown(self):self.tmp.cleanup()
    def preview(self, p=None, name='handoff.zip', limits=dp.Limits()):return dp.preview(self.source,p or self.plan,self.dest,name,limits)
    def build(self, p=None, name='handoff.zip', limits=dp.Limits()):
        p=p or self.plan;approval=self.preview(p,name,limits)['approval_sha256'];return dp.build(self.source,p,self.dest,name,approval,limits)
    def assertNoOutput(self):self.assertEqual(list(self.dest.iterdir()),[])

    def test_preview_is_read_only_and_selective(self):
        report=self.preview();self.assertFalse(report['writes']);self.assertEqual({r['path'] for r in report['manifest']['files']},{'draft.txt','final/read me.txt'});self.assertNoOutput()
    def test_archive_roundtrip_includes_only_approved_bytes_and_declarations(self):
        result=self.build();self.assertEqual(result['outbound'],'none');self.assertEqual(result['destination_delivery'],'not_performed')
        with zipfile.ZipFile(self.dest/'handoff.zip') as z:
            self.assertEqual(set(z.namelist()),{'assets/draft.txt','assets/final/read me.txt',dp.MANIFEST});self.assertEqual(z.read('assets/draft.txt'),(self.source/'draft.txt').read_bytes())
            m=json.loads(z.read(dp.MANIFEST));self.assertEqual(m['plan'],self.plan);self.assertEqual(m['files'][0]['role'],'editable');self.assertNotIn(str(self.source),z.read(dp.MANIFEST).decode())
        self.assertEqual(dp.verify(self.dest/'handoff.zip',result['sha256'])['status'],'verified')
    def test_deterministic_archive_bytes_for_same_plan_content(self):
        self.build();first=(self.dest/'handoff.zip').read_bytes();(self.dest/'handoff.zip').unlink();self.build();self.assertEqual(first,(self.dest/'handoff.zip').read_bytes())
    def test_source_change_after_preview_is_blocked(self):
        approval=self.preview()['approval_sha256'];(self.source/'draft.txt').write_text('A different draft.')
        with self.assertRaises(dp.DeliveryError):dp.build(self.source,self.plan,self.dest,'handoff.zip',approval)
        self.assertNoOutput()
    def test_approval_binds_destination_and_name(self):
        approval=self.preview()['approval_sha256'];other=self.base/'other';other.mkdir()
        for dest,name in [(other,'handoff.zip'),(self.dest,'different.zip')]:
            with self.assertRaises(dp.DeliveryError):dp.build(self.source,self.plan,dest,name,approval)
        self.assertNoOutput();self.assertEqual(list(other.iterdir()),[])
    def test_no_overwrite_file_and_dangling_symlink(self):
        target=self.dest/'handoff.zip';target.write_text('KEEP')
        with self.assertRaises(dp.DeliveryError):self.build()
        self.assertEqual(target.read_text(),'KEEP');target.unlink();target.symlink_to(self.base/'missing')
        with self.assertRaises(dp.DeliveryError):self.preview()
        self.assertTrue(target.is_symlink())
    def test_traversal_absolute_glob_and_private_paths_refused(self):
        for name in ['../secret','/etc/passwd','a/../draft.txt','a//b','./draft.txt','a\\b','*.txt','.git/config','.research/market.txt','.env.example']:
            p=plan();p['assets'][0]['path']=name
            with self.subTest(name=name),self.assertRaises(dp.DeliveryError):self.preview(p)
        self.assertNoOutput()
    def test_source_leaf_symlink_refused(self):
        (self.source/'draft.txt').unlink();(self.source/'draft.txt').symlink_to(self.source/'not-approved.txt')
        with self.assertRaises(OSError):self.preview()
        self.assertNoOutput()
    def test_ancestor_symlink_refused(self):
        elsewhere=self.base/'elsewhere';elsewhere.mkdir();(elsewhere/'read me.txt').write_text('Outside')
        (self.source/'final/read me.txt').unlink();(self.source/'final').rmdir();(self.source/'final').symlink_to(elsewhere,target_is_directory=True)
        with self.assertRaises(OSError):self.preview()
        self.assertNoOutput()
    def test_declared_root_and_destination_symlinks_refused(self):
        linked=self.base/'linked';linked.symlink_to(self.source,target_is_directory=True)
        with self.assertRaises(dp.DeliveryError):dp.preview(linked,self.plan,self.dest,'handoff.zip')
        linked.unlink();linked.symlink_to(self.dest,target_is_directory=True)
        with self.assertRaises(dp.DeliveryError):dp.preview(self.source,self.plan,linked,'handoff.zip')
    def test_fifo_and_directory_not_read_as_assets(self):
        for kind in ['fifo','directory']:
            (self.source/'draft.txt').unlink()
            if kind=='fifo':os.mkfifo(self.source/'draft.txt')
            else:(self.source/'draft.txt').mkdir()
            with self.assertRaises(dp.DeliveryError):self.preview()
            if kind=='directory':(self.source/'draft.txt').rmdir();(self.source/'draft.txt').touch()
        self.assertNoOutput()
    def test_empty_file_is_allowed_with_separate_metadata_budget(self):
        (self.source/'draft.txt').write_bytes(b'');p=plan();p['assets']=p['assets'][:1];limits=dp.Limits(max_bytes=1)
        self.build(p,limits=limits);self.assertEqual(dp.verify(self.dest/'handoff.zip',limits=limits)['files'],1)
    def test_empty_allowlist_duplicate_and_case_collision_refused(self):
        for assets in [[],[self.plan['assets'][0]]*2,[self.plan['assets'][0],{**self.plan['assets'][0],'path':'DRAFT.txt'}]]:
            p=plan();p['assets']=assets
            with self.assertRaises(dp.DeliveryError):self.preview(p)
        self.assertNoOutput()
    def test_byte_and_file_limits_are_real_bounds(self):
        with self.assertRaises(dp.DeliveryError):self.preview(limits=dp.Limits(max_bytes=1))
        with self.assertRaises(dp.DeliveryError):self.preview(limits=dp.Limits(max_files=1))
        for value in [0,-1,True,1.5,float('nan'),float('inf'),513*1024*1024]:
            with self.subTest(value=value),self.assertRaises(dp.DeliveryError):dp.Limits(max_bytes=value)
        self.assertNoOutput()
    def test_closed_metadata_and_delivery_evidence_declarations(self):
        for change in [lambda p:p.update(secret='not a supported field'),lambda p:p.update(owner=' '),lambda p:p['assets'][0].update(role='published'),lambda p:p['assets'][0].update(credits=[]),lambda p:p['destination'].update(status='reported_received')]:
            p=plan();change(p)
            with self.assertRaises(dp.DeliveryError):self.preview(p)
    def test_input_json_duplicate_keys_and_nonfinite_are_refused(self):
        for raw in [b'{"x":1,"x":2}',b'{"x":NaN}',b'{"x":Infinity}']:
            with self.assertRaises(dp.DeliveryError):dp.decode(raw)
    def test_write_failure_cleans_only_own_stage(self):
        approval=self.preview()['approval_sha256']
        with patch.object(zipfile.ZipFile,'writestr',side_effect=OSError('fictional full disk')):
            with self.assertRaises(OSError):dp.build(self.source,self.plan,self.dest,'handoff.zip',approval)
        self.assertNoOutput();self.assertTrue((self.source/'draft.txt').exists())
    def test_late_destination_race_preserves_other_writer(self):
        approval=self.preview()['approval_sha256'];real=os.link
        def race(*args,**kwargs):
            (self.dest/'handoff.zip').write_text('OTHER WRITER');return real(*args,**kwargs)
        with patch.object(dp.os,'link',side_effect=race):
            with self.assertRaises(FileExistsError):dp.build(self.source,self.plan,self.dest,'handoff.zip',approval)
        self.assertEqual((self.dest/'handoff.zip').read_text(),'OTHER WRITER');self.assertEqual(len(list(self.dest.iterdir())),1)
    def test_incorrect_external_archive_digest_is_refused(self):
        self.build()
        with self.assertRaises(dp.DeliveryError):dp.verify(self.dest/'handoff.zip','0'*64)
    def test_archive_change_between_digest_and_payload_is_refused(self):
        first=self.build();target=self.dest/'handoff.zip'
        (self.source/'draft.txt').write_bytes(b'Bn editable fictional draft.\n');self.build(name='second.zip')
        replacement=(self.dest/'second.zip').read_bytes();real=hashlib.file_digest
        def change_after_digest(stream,algorithm):
            result=real(stream,algorithm);target.write_bytes(replacement);return result
        with patch.object(dp.hashlib,'file_digest',side_effect=change_after_digest):
            with self.assertRaises(dp.DeliveryError):dp.verify(target,first['sha256'])
    def test_long_utf8_names_and_metadata_roundtrip_with_empty_payload(self):
        p=plan();p['assets']=[]
        directory=self.source
        parts=[]
        for n in range(8):
            component=chr(97+n)*100;parts.append(component);directory=directory/component;directory.mkdir()
        for n in range(96):
            name='f%03d.txt'%n;(directory/name).touch()
            p['assets'].append({'path':'/'.join(parts+[name]),'role':'final','credits':['Fictional author'],'license':'Declared MIT','accessibility':'Plain text'})
        # Additional permitted declarations consume metadata, independently of payload bytes.
        p['unresolved_limits']=['Unverified declaration '+str(n)+'x'*1700 for n in range(36)]
        limits=dp.Limits(max_bytes=1)
        self.build(p,limits=limits)
        self.assertEqual(dp.verify(self.dest/'handoff.zip',limits=limits)['files'],96)
    def test_excessive_or_understated_index_rejected_before_zip_parser(self):
        self.build();target=self.dest/'handoff.zip'
        with zipfile.ZipFile(target,'a') as archive:
            for n in range(140):archive.writestr(dp._info('extra-%03d'%n),b'')
        data=target.read_bytes()
        for understated in [False,True]:
            blob=bytearray(data)
            if understated:
                struct.pack_into('<HH',blob,len(blob)-22+8,3,3)
            target.write_bytes(blob)
            with patch.object(dp.zipfile,'ZipFile',side_effect=AssertionError('ZIP parser must not run')):
                with self.assertRaises(dp.DeliveryError):dp.verify(target)
    def test_tampered_payload_and_extra_entry_are_refused_without_extraction(self):
        self.build()
        with zipfile.ZipFile(self.dest/'handoff.zip') as z:entries=[(i,z.read(i.filename)) for i in z.infolist()]
        for mode in ['payload','extra','meaning']:
            altered=self.dest/(mode+'.zip')
            with zipfile.ZipFile(altered,'w') as z:
                for info,data in entries:
                    if mode=='payload' and info.filename=='assets/draft.txt':data=b'Changed'
                    if mode=='meaning' and info.filename==dp.MANIFEST:
                        m=json.loads(data);m['meaning']='Ownership certified';data=json.dumps(m).encode()
                    z.writestr(info,data)
                if mode=='extra':z.writestr(dp._info('../outside.txt'),b'not approved')
            with self.subTest(mode=mode),self.assertRaises(dp.DeliveryError):dp.verify(altered)
        self.assertFalse((self.base/'outside.txt').exists())
    def test_compressed_or_symlink_members_are_refused(self):
        self.build()
        with zipfile.ZipFile(self.dest/'handoff.zip') as z:entries=[(i,z.read(i.filename)) for i in z.infolist()]
        for mode in ['compressed','symlink']:
            dest=self.dest/(mode+'.zip')
            with zipfile.ZipFile(dest,'w') as z:
                for info,data in entries:
                    info=copy.copy(info)
                    if info.filename=='assets/draft.txt':
                        if mode=='compressed':info.compress_type=zipfile.ZIP_DEFLATED
                        else:info.external_attr=(0o120777)<<16
                    z.writestr(info,data)
            with self.assertRaises(dp.DeliveryError):dp.verify(dest)
    def test_actual_cli_preview_apply_verify_and_error(self):
        p=self.base/'plan.json';p.write_text(json.dumps(self.plan));cli=[sys.executable,str(ROOT/'delivery_packager.py')]
        args=['--root',str(self.source),'--plan',str(p),'--destination',str(self.dest),'--name','handoff.zip']
        preview=subprocess.run(cli+['preview']+args,text=True,capture_output=True,check=True);report=json.loads(preview.stdout)
        built=subprocess.run(cli+['build']+args+['--apply','--expect-plan',report['approval_sha256']],text=True,capture_output=True,check=True);made=json.loads(built.stdout)
        good=subprocess.run(cli+['verify',str(self.dest/'handoff.zip'),'--expected-sha256',made['sha256']],text=True,capture_output=True,check=True);self.assertEqual(json.loads(good.stdout)['status'],'verified')
        bad=subprocess.run(cli+['build']+args+['--apply','--expect-plan',report['approval_sha256']],text=True,capture_output=True);self.assertEqual(bad.returncode,2);self.assertEqual(json.loads(bad.stdout)['status'],'blocked');self.assertEqual(bad.stderr,'')
    def test_standalone_copied_module_cli_works(self):
        copy=self.base/'standalone';copy.mkdir();(copy/'delivery_packager.py').write_bytes((ROOT/'delivery_packager.py').read_bytes())
        result=subprocess.run([sys.executable,str(copy/'delivery_packager.py'),'--version'],cwd=copy,text=True,capture_output=True,check=True);self.assertEqual(result.stdout.strip(),'0.1.0')

if __name__=='__main__':unittest.main()
