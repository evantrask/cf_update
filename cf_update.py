#!/usr/bin/env python3
# Written 24 Jul 2025 by etrask

# Improvement ideas:
# - Break this up into actual functions, idiot
# - PEP8 this thing? do we care?

import requests
import json
import ipaddress
import datetime
import zoneinfo
from os import path

# We don't want to put private IPs in public DNS, but there may be some use case
ALLOW_PRIVATE_IPS=False

# Our input JSON. This is the file containing the records to be updated, as well as API keys
input_file="/home/etrask/code/scripts/cf_update/cloudflare_settings2.json"

# Cloudflare API URLs. Keep placeholder values!
cf_url_token_verification="https://api.cloudflare.com/client/v4/user/tokens/verify"
cf_url_zone_list="https://api.cloudflare.com/client/v4/zones"
cf_url_record_list="https://api.cloudflare.com/client/v4/zones/{zone_id}/dns_records"
cf_url_record="https://api.cloudflare.com/client/v4/zones/{zone_id}/dns_records/{record_id}"



# ANSI escape codes for pretty text colors:
class col:
    R = '\033[91m'
    G = '\033[92m'
    B = '\033[94m'
    C = '\033[0m'



# Get our public IPv4 and do some sanity checking
external_ip=ipaddress.ip_address(requests.get('https://api.ipify.org').text)
print("IPv4 address returned from IPify: "+col.B+str(external_ip)+col.C)
if external_ip.is_private and not ALLOW_PRIVATE_IPS:
    print("IPv4 address is in private range (RFC1918) and should not be placed in pubic DNS."
          +col.R+"Aborting update!"+col.C)
    exit()
elif external_ip.is_reserved:
    print("IPv4 address is in IETF reserved range (240.0.0.0/4)."
          +col.R+"Aborting update!"+col.C)
    exit()
else:
    print("IPv4 address looks good, proceeding...")




# Read our input file into a Python dict
if not path.isfile(input_file):
    print("Cannot access input file:"+input_file+col.R+"\nAborting!"+col.C)
    exit()
else:
    # We could probably do this as a one-liner but I like an explicit file close
    fd = open(input_file, "r")
    cloudflare_settings = json.load(fd)
    fd.close()
    print(col.G+"Successfully "+col.C+"loaded "+col.B+input_file+col.C)

num_zones=len(cloudflare_settings["zones"])
num_records=0

for recordset in cloudflare_settings["zones"]:
    num_records+=len(recordset["records"])

print("Found "+col.B+str(num_records)+col.C+" records across "+col.B
      +str(num_zones)+col.C+" zones")

print("\n")

# Use a dedicated session for Cloudflare requests
with requests.Session() as s:
    for zone in cloudflare_settings["zones"]:
        # All requests need to include token header
        s.headers.clear()
        s.headers.update({"Authorization":"Bearer "+zone["api_token"]})
        print("Begin processing zone "+col.B+zone["name"]+col.C)

        # Check for api_token validity
        r_token_verification=json.loads(s.get(cf_url_token_verification).text)
        if not r_token_verification["success"]:
            print("API token provided for "+col.B+zone["name"]+col.C+" is "
                  +col.R+"NOT VALID"+col.C+". Continuing to next zone...")
            continue
        print("The provided Cloudflare API token is a "\
                +col.G+"valid, active token!"+col.C)

        # Get a list of zones to which this key has access
        r_zone_list=json.loads(s.get(cf_url_zone_list).text)

        # All of these have to be true for us to proceed
        token_can_read_zone=False
        token_can_edit_zone=False
        cf_zone_id=""

        # Check that zone is here, and we have read and edit
        for cf_zone in r_zone_list["result"]:
            zone_is_here=zone["name"] in cf_zone.values()
            if zone_is_here:
                cf_zone_id=cf_zone["id"]
                token_can_read_zone = token_can_read_zone or zone_is_here
            else:
                print(col.B+zone["name"]+col.C+" is not in the list returned by "\
                        +"Cloudflare. We have to skip it...")
                continue
            if not token_can_read_zone:
                continue
            for cf_permissions in cf_zone["permissions"]:
                token_can_edit_zone = token_can_edit_zone \
                    or ("\x23dns_records:edit" in cf_permissions)

        if not token_can_read_zone:
            print("API token "+col.R+"CANNOT "+col.C+"read "+col.B+zone["name"]
                  +col.C+". Continuing to next zone...")
            continue
        if not token_can_edit_zone:
            print("API token "+col.R+"CANNOT "+col.C+"edit "+col.B+zone["name"]
                  +col.C+". Continuing to next zone...")
            continue

        print("API token can read and edit "+col.B+zone["name"]+col.C+" (zone id: "
              +col.B+cf_zone_id+col.C+")! Proceeding...")

        # Get all DNS records for the zone
        r_all_records=json.loads(s.get(cf_url_record_list.replace("{zone_id}",cf_zone_id)).text)
        num_records=len(r_all_records["result"])

        # Create an array containing only the A records
        a_records=[]
        for a_rec in r_all_records["result"]:
            if a_rec["type"] == "A":
                a_records.append(a_rec)
        num_a_records=len(a_records)
        print(col.B+str(num_records)+col.C+" DNS records found in zone, of which "
              +col.B+str(num_a_records)+col.C+" are A records")

        # Cycle through the A records
        for a_rec in a_records:
            # If the record we want to update is in the list returned from CF:
            if a_rec["name"] in zone["records"]:
                print("Found target DNS record "+col.B+a_rec["name"]+col.C
                      +" (record ID: "+col.B+a_rec["id"]+col.C+")")
                # Found the record. Does it need to be updated?
                if a_rec["content"] == str(external_ip):
                    print("DNS record matches current IP. No updated needed!")
                else:
                    print("DNS record ("+col.B+a_rec["content"]+col.C
                          +") does not match external IP ("+col.B+str(external_ip)
                          +col.C+"). Update needed!")

                    # Construct comment with timestamp
                    comment='Updated by cf_update '+str(datetime.datetime.now(
                        zoneinfo.ZoneInfo('America/Los_Angeles')))

                    # Construct JSON payload
                    record_update_json='{"comment": "{comment}","content": "{external_ip}"}'.replace(
                            "{comment}",comment).replace(
                                    "{external_ip}",str(external_ip))

                    # Add Content-Type header to session
                    s.headers.update({'Content-Type':'application/json'})

                    # Construct PATCH URL
                    patch_url=cf_url_record.replace("{zone_id}",cf_zone_id).replace("{record_id}",a_rec["id"])

                    # Send PATCH request via session
                    patch_results=json.loads(s.patch(url=patch_url,data=record_update_json).text)

                    # Did it work?
                    if patch_results["success"]==True:
                        print(col.B+a_rec["name"]+col.C+" updated successfully!")
                    else:
                        print(col.B+a_rec["name"]+col.C+" was not updated successfully. Patch results follow:\n")
                        print(patch_results)

            # Problem: if the record we want to update isn't found, there's no indication of that. No warning, etc

        print("\n")

print("We're done bye bye")
