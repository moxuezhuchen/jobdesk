"""Exact v2 schema fallback for the approved ConfFlow 2.0.0 wheel."""

from __future__ import annotations

import base64
import gzip

SCHEMA_SHA256 = "c2bd1799ea8bab53b5435b56aff8dcc57f35cc32142b76eb40eea0617e7cef83"
SCHEMA_ID = "https://confflow.dev/schemas/config/v2/workflow.schema.json"
FIXTURE_SET_ID = "confflow.config_contract.v2"
FIXTURE_MANIFEST_SHA256 = "21a72b9deeabd94c4d94582b4424c273acbb02a763dbc17a9582271f13a71b84"

_COMPRESSED_SCHEMA = b"""ABzY8000000{`t@>u=*W693-6LeS;IKCIZu76&v%5$r9lkLzO#Y`+{7oj_A#n`??xNh*mqyZ`$QDanzYI97_L*rWpl4owbchBLzvA3w-f@j(GI8=qXo)<Na}v!W1AXvO5OMps~*0Bm=rhEZJgYOZ2XY$~Df#R4ohqVoOXllYBRvu~xoh|Z5AuEB7uK8dDUOJFLIVhM-wD85WM(#?=A>4>mzpDO+|iiN6bXNSs=FlT&~RLokpFuI;Ec1yZR>*Xm{!cb|}8KLHhHpv_m80MhZE$$X#FRx;Tm4nAuvZ`viKv}ufZgJa`UY1)`gU5#2mL(JkHeeOAkoW*eJPR+hV$Ol_-T!xXW?wu@`I{`&Z?jbWlBKh6vvmGlmcqZo_m82Gz0OkkQ|8MbH(T9rO{;yErOO|)=bqs~+^O3dZgZea3RDTrmd0pJPs{8w&UP8&j=^m5T`GN{5~WRv5<Xy$b0+IXj;Cp4aVB}3eL{m8_`Md!Pl2@2Hd|}0r|*HgP40zYbET~lJn8ogA=h>cY!3w1#|!5wdomt(-46>mA6veFlG#ux=Hv6@@r5?a(A+c~zPH*ddC+|A@$n5wOQg+wTo^WU<KxNkBsn^68Dq!c^M5Y&l*y1Y)avaFhjO3n!KC*_k;5hM+WAak??3M>nh_L-A=9p-^)rFhPUqYwnupC^Q>pp#4~(mwy+el8l6q-;!OR>s9nyxQs>>-DB)Zz3y6MX#J549aSJ|`Ele{TCe{uU9^xfN<cBJtq4EZ5r6UxCH%<d7j1e@(`j&>-^Fz>sQ__qbLY2FKvniXI$FRmiSIaDsXj=<>Gm0M^P;n=|l-kLX>;q10wM-KUvj4y-&R8u*_xQl8`DiLQ&D}l2-dj0Or4?kS@Un$2f4}+z(0h?nq=OOWISiBTUlyw=O9F6u{|LpAL>9f;syw&sUMSA3oe-CG%;?_0uYp5eCZP7mGknHuyHs#}RO#u=d<UTFtg)k30zndjcUaT;O3!Y<8=-Tk%ne2~B1v6jeJ=5QR#$$Zp-;Gdvj$h8s4sUHbeSZ3JSDWt_p6D&_TPRNd^qhirVs5wDLi6JY+Sa2NLKXUAhdZV0+v;!NUR^bIPxknElkRai9C>iaefk!-6jglnuo+`!06uKx38kH{Xr051VYo@~?h+1~9ZA!aUa!F0pc}1SnXPvf9fN_sWlop)Tq)pkz#8rrVn3)ktF0AG<v0Nr_giD_TUc$dzc6T#oG+F`Wvl~on6qvrU+;ar;QKtt*4!)xAj4(vdcgbLIcm~OwpY^)-a%Dd+|P%q=xK4+n@BbVdB^&rPqwv_(j^lr!Tp5lb{E5Tar2{pA}5@DY-^MqHET4#ijPo(hWgl#-1oqkn7mq7&nI79m+^C5W_7<DVrX3v__XZ&R!?;6$3c5?q}^w<YnngFV(4?^WUDx2k>C9oDwnrKXZM+L(u57tdV>Zw-kxp$S50fj?n^%a+etd<30!koQ*U&{;>}7F)=KmTNdCiHd<iRD8y5GUPJ0r^7NMUMCA-Z1`Hqwz^Y?mcX`}ENJ%YV=F3>l*6D8ypkU?Q-?e+B6Ug&tge;=9w+Mo_cNVnyH-dSYFdFuh%sg%qF$f8aEc}KkGK;&9((7l#UR8nx^R+LRXqo7jy`-fe^Z^X=B7P@=FF(~yg%Wne~t&GodUFK89mv!~Prz#i&MeY_TccEoLXDW~VOvN%)O!Z}MbhFX@d^b;6p>nR7vA7QDu3;(m9&_w@kIt|w`Sa=cv#u5bw+m8~!qFw$Utn~ntDG-@FLSTAIcIXuHjQS~qnS}oJ9K9Wr8a}lz8mV^LwVcH-taFQe>UaD1>QRzaWef^_AxDgZVSsF+X8+J!JPELHGM@MiM?$;#hH`)vvfOr|A2E&6p3B13hedyAPz6_i=Dd?&D}lwDiub|9Id>c(A>8ECY;x}`r!|au<^2J<MxEY=hHTX&f8@3uCi;mX`1f>B(2@Xp&lr!14!A(fMY@h-EeRL6`2$;W3_bA6c+4UXmbxh<p!a_b;(?Xs}q8`5zLKXZgk#EFgJp^(UYBEZUl29m>a>|2<ApGH-fnl%#Ah*1aqUc7s1>J=0-3#g1HgQjm}gE=0>qcFgNli!Q2SuMld&mxe?5bU~V+G3FbyHw<isA3r~Kiwx03pOUVNRSQGza9A3~k4(sb1w~(WoXSeN{)r?6Cz3-D=ISzy+yX?QBZE^#4w{wRVc(42ki4@i~h&|yX^nKR7l`d*tR9BmDQ-aox?|eg^f3;o`UWwS=<OEx^w{B^C6s}c9+qX{bebeY@beyg4n$ma@ZeyArufnxU|88gVhNrtf8BIKw(W}vy{?2z}{Ed5)b1*j8;dscq{n~frb-v!TY17yoml9Zzz=8x8q*HqW3ldn6)<*;uB(NZX1qm!jU_k;45?GMHg0xW}upq6y2rNioK>`aBSdhSibf!XJL5e*B3z9zxEJ$EM0t*sYkidcj7NogNU_k;4KFxRH-L0e7D(^6Nd8rGK?d$H4`+|8<$ngUdehAG^Dn}YWL;<Lmus0O@`dY=1v>N!H0T0=;;kA1IdH430=)bRj{wZqiMWQL-Kp|>=NHzjr8+vWoziHnEqq`6W+ItxsR`|(Uu1kzhF_n<4SczHki~j=vF4PgWdH?_"""


def schema_bytes() -> bytes:
    """Return the frozen canonical bytes after verifying their digest."""

    import hashlib

    value = gzip.decompress(base64.b85decode(_COMPRESSED_SCHEMA))
    if hashlib.sha256(value).hexdigest() != SCHEMA_SHA256:
        raise RuntimeError("checked-in stable configuration schema is corrupt")
    return value


__all__ = [
    "FIXTURE_MANIFEST_SHA256",
    "FIXTURE_SET_ID",
    "SCHEMA_ID",
    "SCHEMA_SHA256",
    "schema_bytes",
]
