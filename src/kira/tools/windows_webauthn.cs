// Private pipe-only Windows WebAuthn client. No credential enumeration or storage.
// Struct layouts follow microsoft/webauthn/webauthn.h (Win32 ABI, default packing).
using System;
using System.Collections;
using System.Collections.Generic;
using System.Drawing;
using System.Runtime.InteropServices;
using System.Text;
using System.Threading;
using System.Web.Script.Serialization;
using System.Windows.Forms;

public static class KiraWebAuthn {
    [StructLayout(LayoutKind.Sequential)] struct Pair { public uint count; public IntPtr values; }
    [StructLayout(LayoutKind.Sequential)] struct Rp { public uint version; public IntPtr id, name, icon; }
    [StructLayout(LayoutKind.Sequential)] struct User {
        public uint version, size; public IntPtr id, name, icon, displayName;
    }
    [StructLayout(LayoutKind.Sequential)] struct ClientData {
        public uint version, size; public IntPtr json, hash;
    }
    [StructLayout(LayoutKind.Sequential)] struct Cose {
        public uint version; public IntPtr type; public int algorithm;
    }
    [StructLayout(LayoutKind.Sequential)] struct Credential {
        public uint version, size; public IntPtr id, type;
    }
    [StructLayout(LayoutKind.Sequential)] struct CredentialEx {
        public uint version, size; public IntPtr id, type; public uint transports;
    }
    // Make v4 preserves preferred resident keys; supported from API v3.
    [StructLayout(LayoutKind.Sequential)] struct MakeOptions {
        public uint version, timeout; public Pair credentials, extensions;
        public uint attachment; public int requireResident; public uint verification, attestation, flags;
        public IntPtr cancellation, exclude; public uint enterprise, largeBlob; public int preferResident;
    }
    [StructLayout(LayoutKind.Sequential)] struct GetOptions {
        public uint version, timeout; public Pair credentials, extensions;
        public uint attachment, verification, flags;
        public IntPtr appId, appIdUsed, cancellation, allow;
    }
    // Only the stable prefix through attestation v4 is read.
    [StructLayout(LayoutKind.Sequential)] struct Attestation {
        public uint version; public IntPtr format;
        public uint authDataSize; public IntPtr authData;
        public uint attestationSize; public IntPtr attestation;
        public uint decodeType; public IntPtr decoded;
        public uint objectSize; public IntPtr attestationObject;
        public uint credentialSize; public IntPtr credential;
        public Pair extensions; public uint transport;
        public int enterprise, largeBlob, resident;
    }
    [StructLayout(LayoutKind.Sequential)] struct Assertion {
        public uint version, authDataSize; public IntPtr authData;
        public uint signatureSize; public IntPtr signature;
        public Credential credential; public uint userSize; public IntPtr user;
    }
    [DllImport("webauthn.dll", ExactSpelling=true)] static extern uint WebAuthNGetApiVersionNumber();
    [DllImport("webauthn.dll", ExactSpelling=true)] static extern int WebAuthNIsUserVerifyingPlatformAuthenticatorAvailable(out int available);
    [DllImport("webauthn.dll", ExactSpelling=true)] static extern int WebAuthNGetCancellationId(out Guid id);
    [DllImport("webauthn.dll", ExactSpelling=true)] static extern int WebAuthNCancelCurrentOperation(ref Guid id);
    [DllImport("webauthn.dll", ExactSpelling=true)] static extern IntPtr WebAuthNGetErrorName(int result);
    [DllImport("webauthn.dll", ExactSpelling=true)] static extern int WebAuthNAuthenticatorMakeCredential(
        IntPtr window, ref Rp rp, ref User user, ref Pair algorithms, ref ClientData data,
        ref MakeOptions options, out IntPtr attestation);
    [DllImport("webauthn.dll", ExactSpelling=true, CharSet=CharSet.Unicode)] static extern int WebAuthNAuthenticatorGetAssertion(
        IntPtr window, string rp, ref ClientData data, ref GetOptions options, out IntPtr assertion);
    [DllImport("webauthn.dll", ExactSpelling=true)] static extern void WebAuthNFreeCredentialAttestation(IntPtr value);
    [DllImport("webauthn.dll", ExactSpelling=true)] static extern void WebAuthNFreeAssertion(IntPtr value);

    sealed class Arena : IDisposable {
        readonly List<KeyValuePair<IntPtr,int>> blocks = new List<KeyValuePair<IntPtr,int>>();
        public IntPtr Allocate(int size) {
            if (size < 1 || size > 262144) throw new ArgumentException();
            IntPtr ptr=Marshal.AllocHGlobal(size);
            blocks.Add(new KeyValuePair<IntPtr,int>(ptr,size));
            for(int i=0;i<size;i++) Marshal.WriteByte(ptr,i,0);
            return ptr;
        }
        public IntPtr Bytes(byte[] bytes) {
            IntPtr ptr=Allocate(Math.Max(1,bytes.Length));
            Marshal.Copy(bytes,0,ptr,bytes.Length); return ptr;
        }
        public IntPtr Text(string value) { return Bytes(Encoding.Unicode.GetBytes(value+"\0")); }
        public IntPtr Structure(object value) {
            IntPtr ptr=Allocate(Marshal.SizeOf(value)); Marshal.StructureToPtr(value,ptr,false); return ptr;
        }
        public void Dispose() {
            foreach(var block in blocks) {
                for(int i=0;i<block.Value;i++) Marshal.WriteByte(block.Key,i,0);
                Marshal.FreeHGlobal(block.Key);
            }
            blocks.Clear();
        }
    }

    static readonly object gate=new object(), outputGate=new object();
    static Thread operationThread;
    static System.Threading.Timer cancelTimer;
    static bool running, cancelled;
    static string currentNonce;
    static Guid cancellation;
    static JavaScriptSerializer Serializer() { return new JavaScriptSerializer { MaxJsonLength=262144, RecursionLimit=32 }; }
    static Dictionary<string,object> Obj(params object[] pairs) {
        var result=new Dictionary<string,object>();
        for(int i=0;i<pairs.Length;i+=2) result.Add((string)pairs[i],pairs[i+1]); return result;
    }
    static Dictionary<string,object> Dict(object value) { return (Dictionary<string,object>)value; }
    static string Text(Dictionary<string,object> value,string key) { return (string)value[key]; }
    static uint Number(Dictionary<string,object> value,string key) { return Convert.ToUInt32(value[key]); }
    static bool Flag(Dictionary<string,object> value,string key) { return (bool)value[key]; }
    static byte[] Decode(string value) {
        string b64=value.Replace('-','+').Replace('_','/');
        return Convert.FromBase64String(b64.PadRight((b64.Length+3)/4*4,'='));
    }
    static string Encode(byte[] value) { return Convert.ToBase64String(value).TrimEnd('=').Replace('+','-').Replace('/','_'); }
    static byte[] Read(IntPtr pointer,uint size) {
        if(size>131072 || (size>0 && pointer==IntPtr.Zero)) throw new ArgumentException();
        var bytes=new byte[size]; if(size>0) Marshal.Copy(pointer,bytes,0,(int)size); return bytes;
    }
    static void Write(object value) {
        string line=Serializer().Serialize(value);
        if(line.Length>262144) throw new ArgumentException();
        lock(outputGate) { Console.Out.WriteLine(line); Console.Out.Flush(); }
    }
    static Dictionary<string,object> Error(string nonce,string name) {
        return Obj("type","result","nonce",nonce,"ok",false,
                   "error",Obj("name",name,"message","Windows authentication did not complete"));
    }
    static string ErrorName(int code) {
        string name=Marshal.PtrToStringUni(WebAuthNGetErrorName(code));
        switch(name) {
            case "NotAllowedError": case "NotSupportedError": case "InvalidStateError":
            case "SecurityError": case "ConstraintError": case "AbortError": return name;
            default: return "UnknownError";
        }
    }
    static IntPtr CredentialList(Arena arena,object items) {
        var list=(IList)items;
        if(list.Count==0) return IntPtr.Zero;
        IntPtr pointers=arena.Allocate(list.Count*IntPtr.Size);
        for(int i=0;i<list.Count;i++) {
            var value=Dict(list[i]); byte[] id=Decode(Text(value,"id"));
            var item=new CredentialEx { version=1, size=(uint)id.Length,id=arena.Bytes(id),
                type=arena.Text("public-key"),transports=Number(value,"transports") };
            Marshal.WriteIntPtr(pointers,i*IntPtr.Size,arena.Structure(item));
        }
        return arena.Structure(new Pair {count=(uint)list.Count,values=pointers});
    }
    static Pair Algorithms(Arena arena,object items) {
        var list=(IList)items; int size=Marshal.SizeOf(typeof(Cose));
        IntPtr start=arena.Allocate(size*list.Count);
        for(int i=0;i<list.Count;i++) {
            var item=new Cose {version=1,type=arena.Text("public-key"),algorithm=Convert.ToInt32(list[i])};
            Marshal.StructureToPtr(item,IntPtr.Add(start,i*size),false);
        }
        return new Pair {count=(uint)list.Count,values=start};
    }
    static string[] Transports(uint transport) {
        var result=new List<string>();
        if((transport&1)!=0)result.Add("usb"); if((transport&2)!=0)result.Add("nfc");
        if((transport&4)!=0)result.Add("ble"); if((transport&16)!=0)result.Add("internal");
        if((transport&32)!=0)result.Add("hybrid"); return result.ToArray();
    }
    static Dictionary<string,object> Make(IntPtr window,Dictionary<string,object> value,Arena arena,Guid cancel) {
        var rp=new Rp {version=1,id=arena.Text(Text(value,"rpId")),name=arena.Text(Text(value,"rpName"))};
        byte[] userId=Decode(Text(value,"userId")), json=Decode(Text(value,"clientData"));
        var user=new User {version=1,size=(uint)userId.Length,id=arena.Bytes(userId),
            name=arena.Text(Text(value,"userName")),displayName=arena.Text(Text(value,"userDisplayName"))};
        var client=new ClientData {version=1,size=(uint)json.Length,json=arena.Bytes(json),hash=arena.Text("SHA-256")};
        Pair algorithms=Algorithms(arena,value["algorithms"]);
        var options=new MakeOptions {version=4,timeout=Number(value,"timeout"),
            attachment=Number(value,"attachment"),verification=Number(value,"userVerification"),
            attestation=Number(value,"attestation"),requireResident=Flag(value,"requireResidentKey")?1:0,
            preferResident=Flag(value,"preferResidentKey")?1:0,cancellation=arena.Structure(cancel),
            exclude=CredentialList(arena,value["credentials"])};
        IntPtr pointer=IntPtr.Zero;
        try {
            int status=WebAuthNAuthenticatorMakeCredential(window,ref rp,ref user,ref algorithms,ref client,ref options,out pointer);
            if(status!=0) return Error(currentNonce,ErrorName(status));
            if(pointer==IntPtr.Zero || Marshal.ReadInt32(pointer)<4) throw new InvalidOperationException();
            var att=(Attestation)Marshal.PtrToStructure(pointer,typeof(Attestation));
            string id=Encode(Read(att.credential,att.credentialSize));
            if(id.Length==0) throw new InvalidOperationException();
            var extensions=Obj();
            if(Flag(value,"credProps")) extensions.Add("credProps",Obj("rk",att.resident!=0));
            var credential=Obj("id",id,"rawId",id,"type","public-key", "clientExtensionResults",extensions,
                "response",Obj("clientDataJSON",Text(value,"clientData"),
                    "authenticatorData",Encode(Read(att.authData,att.authDataSize)),
                    "attestationObject",Encode(Read(att.attestationObject,att.objectSize)),
                    "transports",Transports(att.transport)));
            if(att.transport!=0) credential.Add("authenticatorAttachment",(att.transport&16)!=0?"platform":"cross-platform");
            return Obj("type","result","nonce",currentNonce,"ok",true,"response",credential);
        } finally { if(pointer!=IntPtr.Zero) WebAuthNFreeCredentialAttestation(pointer); }
    }
    static Dictionary<string,object> Get(IntPtr window,Dictionary<string,object> value,Arena arena,Guid cancel) {
        byte[] json=Decode(Text(value,"clientData"));
        var client=new ClientData {version=1,size=(uint)json.Length,json=arena.Bytes(json),hash=arena.Text("SHA-256")};
        var options=new GetOptions {version=4,timeout=Number(value,"timeout"),
            verification=Number(value,"userVerification"),cancellation=arena.Structure(cancel),
            allow=CredentialList(arena,value["credentials"])};
        IntPtr pointer=IntPtr.Zero;
        try {
            int status=WebAuthNAuthenticatorGetAssertion(window,Text(value,"rpId"),ref client,ref options,out pointer);
            if(status!=0) return Error(currentNonce,ErrorName(status));
            if(pointer==IntPtr.Zero || Marshal.ReadInt32(pointer)<1) throw new InvalidOperationException();
            var assertion=(Assertion)Marshal.PtrToStructure(pointer,typeof(Assertion));
            string id=Encode(Read(assertion.credential.id,assertion.credential.size));
            if(id.Length==0) throw new InvalidOperationException();
            var response=Obj("clientDataJSON",Text(value,"clientData"),
                "authenticatorData",Encode(Read(assertion.authData,assertion.authDataSize)),
                "signature",Encode(Read(assertion.signature,assertion.signatureSize)),
                "userHandle",assertion.userSize==0?null:Encode(Read(assertion.user,assertion.userSize)));
            return Obj("type","result","nonce",currentNonce,"ok",true,"response",
                Obj("id",id,"rawId",id,"type","public-key","clientExtensionResults",Obj(),"response",response));
        } finally { if(pointer!=IntPtr.Zero) WebAuthNFreeAssertion(pointer); }
    }
    static void CancelTick(object unused) {
        Guid id; lock(gate) { if(!running || !cancelled)return; id=cancellation; }
        WebAuthNCancelCurrentOperation(ref id);
    }
    static void Cancel(string nonce) {
        lock(gate) {
            if(!running || (nonce!=null && nonce!=currentNonce))return;
            cancelled=true;
            if(cancelTimer==null)cancelTimer=new System.Threading.Timer(CancelTick,null,0,50);
        }
    }
    static void Perform(Dictionary<string,object> request,string nonce) {
        Dictionary<string,object> result;
        try {
            Guid cancel; lock(gate) { if(cancelled)throw new OperationCanceledException();cancel=cancellation; }
            using(var arena=new Arena()) using(var owner=new Form()) {
                // This window belongs only to this manual authentication operation.
                // Its native Windows security UI is never captured or automated.
                owner.Text="Kira"; owner.ShowInTaskbar=false; owner.Size=new Size(1,1);
                owner.StartPosition=FormStartPosition.CenterScreen; owner.Opacity=0;
                owner.Show(); owner.Activate();
                lock(gate) {if(cancelled)throw new OperationCanceledException();}
                result=Text(request,"operation")=="create"?Make(owner.Handle,request,arena,cancel):Get(owner.Handle,request,arena,cancel);
            }
        } catch(OperationCanceledException) {result=Error(nonce,"AbortError");}
          catch(Exception) {result=Error(nonce,"OperationError");}
        lock(gate) {
            running=false; if(cancelTimer!=null){cancelTimer.Dispose();cancelTimer=null;}
        }
        Write(result);
    }
    public static void Run() {
        uint version=WebAuthNGetApiVersionNumber(); int available;
        int status=WebAuthNIsUserVerifyingPlatformAuthenticatorAvailable(out available);
        Write(Obj("type","availability","ok",status==0,"apiVersion",version,
                  "platformAuthenticatorAvailable",status==0 && available!=0));
        try {
            string line;
            while((line=Console.In.ReadLine())!=null) {
                if(line.Length>262144)break;
                var message=Serializer().Deserialize<Dictionary<string,object>>(line);
                string command=Text(message,"command");
                if(command=="close")break;
                string nonce=Text(message,"nonce"); Guid parsed;
                if(!Guid.TryParseExact(nonce,"N",out parsed))break;
                if(command=="cancel") {Cancel(nonce);continue;}
                if(command!="perform")break;
                var request=Dict(message["request"]);
                if(Text(request,"operation")!="create" && Text(request,"operation")!="get")break;
                lock(gate) {
                    if(running) {Write(Error(nonce,"NotAllowedError"));continue;}
                    if(WebAuthNGetCancellationId(out cancellation)!=0) {Write(Error(nonce,"UnknownError"));continue;}
                    currentNonce=nonce; cancelled=false; running=true;
                    operationThread=new Thread(()=>Perform(request,nonce));
                    operationThread.IsBackground=true;operationThread.SetApartmentState(ApartmentState.STA);
                    operationThread.Start();
                }
            }
        } finally {
            Cancel(null);
            if(operationThread!=null && !operationThread.Join(3000))Environment.Exit(2);
        }
    }
}
