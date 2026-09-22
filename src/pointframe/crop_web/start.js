"use strict";
const button=document.getElementById("openCloud"),message=document.getElementById("message");
button.onclick=async()=>{
  button.disabled=true;
  message.textContent="Choose a PLY file in the system dialog…";
  try{
    const response=await fetch("/api/open",{method:"POST",headers:{"Content-Type":"application/json"},body:"{}"});
    const result=await response.json();
    if(!response.ok)throw Error(result.error||response.statusText);
    if(result.opened){location.reload();return}
    message.textContent="No file selected.";
  }catch(error){message.textContent=error.message}
  finally{button.disabled=false}
};
