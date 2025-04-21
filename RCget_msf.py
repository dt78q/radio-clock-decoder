'''Decoder for the output of a radio module receiving the long-wavelength
PWM date-time code (Time from NPL, MSF signal) from Anthorn, Cumbria
MSF broadcasts GMT or BST
https://en.wikipedia.org/wiki/Time_from_NPL_(MSF)
Each code is synchronised to the start of the following minute
The RTC will be set to UTC; use loc_time() for geographic local time with daylight saving times
'''
# def RCget():
########## settings ##############
GPIOsig = 1  # input (GP#) for the radio module
input_pull = 'off'  # 'off', 'up' or 'down'  # the input may or may not need a pull up
enable = 'low'  # 'off', 'high' or 'low'  # for if module has an enable/standby control
GPIOen = 2  # output for the enable connection if used

timeout = 15  # minutes

'''For sampling the timecode signal, need delta_t - the offset of the 
millisecond counter (at ...000 ms) from the start of the timecode marker pulse
the millisecond counter is independent of the RTC; setting the RTC will not change delta_t
'''
# set known delta_t ('delay') for testing, otherwise must be set to 'None'
delta_t = None

'''After sampling, two pairs of indices set the pulse parameters in 5 ms steps
they might differ significantly from the specification so tweak these to get clean pulses using show_smpls. 
Should be all ones or all zeros but might vary from time to time so set conservatively (smaller range)
specification is bitA: 100 ms - 200 ms, bitB: 200 ms - 300 ms
bitA has the timecode data, bitB contains the parity bits
'''
show_smpls = True  # use to check for clean pulses
A1, A2 = 27, 45  # bit A, 27,45 = 135-225 ms
B1, B2 = 48, 66  # bit B, 48,66 = 240-330 ms

######### end of settings #######

from machine import Pin, RTC, Timer # type: ignore
from time import time, ticks_ms, ticks_diff, gmtime, mktime, sleep_ms # type: ignore
from array import array
rtc=RTC()

# trig = Pin(4, Pin.OUT)  # to trigger an oscilloscope to monitor the pulses

if input_pull == 'up':
    rc_signal = Pin(GPIOsig, Pin.IN, Pin.PULL_UP)
elif  input_pull == 'down':
    rc_signal = Pin(GPIOsig, Pin.IN, Pin.PULL_DOWN)
else:
    rc_signal = Pin(GPIOsig, Pin.IN)

if enable != 'off':
    rc_enable = Pin(GPIOen, Pin.OUT)  # receiver module enable
    rc_enable.value(0) if enable=='high' else rc_enable.value(1)

# Set interrupt to detect the leading edge of the pulses
def isr_trig(Pin):
    delta[0] = ticks_ms()

# Set interrupt to sample the pulses
def isr_sample(timer):
    samples[0] +=1
    samples[samples[0]] = rc_signal.value()

# Some stats
def mean(data): return sum(data) / len(data)
def mid(data): return (max(data) + min(data))/2
def st_dev(data): return (sum([(x - mean(data))**2 for x in data]) /(len(data)-1))**0.5

# Delete outlier
def del_outlr(data):
    outlier = max(data) if mid(data) > mean(data) else min(data)
    data.remove(outlier)
    # print("removed:", outlier)

# Measure the time offset of the pulses from the RTC 'tick' over period = secs
def get_deltas(secs):
    start = ticks_ms()
    count = secs * 2
    while count > 0  and ticks_diff(ticks_ms(), start)/1000 < secs-1:
        # poll for pulse detected
        if delta[0] != -1:
            count -=1
            deltas.append(delta[0]%1000)
            shifts.append((delta[0]+500)%1000)
            delta[0] = -1
            print(deltas[-8:])
            sleep_ms(50)
    return deltas, shifts

################### Start ################################
print('>>>> rc_msf >>>>')
rc_sync = False
if enable != 'off':
    sleep_ms(50)  # to toggle rc_enable
    rc_enable.value(1) if enable=='high' else rc_enable.value(0)
abort = False

################### Get "delta_t" ########################
if delta_t:
    print("Using set value of delta_t =",delta_t,"ms for testing")
else:
    # Measure delta_t (offset of timecode pulses from millisecond counter ..000 ms)
    rc_signal.irq(trigger=Pin.IRQ_RISING, handler=isr_trig)
    delta = array('i',[-1])  # array avoids both memory allocation and global in ISR (???)
    elapsed = 0
    while True:
        elapsed +=1
        if elapsed == 10:
            print("Waiting for timecode pulses")
        if elapsed == 20:
            print("might take a minute or two if just powered up")
        if delta[0] !=-1:  # pulse received
            delta[0] = -1
            sleep_ms(1000)
            break
        sleep_ms(1000)  # allow agc etc. to settle

    print("Measuring the RTC offset /ms from the timecode pulse")
    '''delta_t is average of (cleaned up) delta values
    occasionally delta_t will be so close to zero (or 999) that some delta%1000 values wrap around
    to prevent this 'shifts' is deltas shifted by 500 ms then we check if values are more consistent
    (shift may also be triggered by extraneous noise pulses)
    In each set of delta_t there will be a normal distribution of values due to jitter but there may 
    also be random noise pulses. In a small dataset outliers can produce a large error in the average
    so are removed
    '''
    deltas, shifts = [],[]
    shift = 0
    period = 16  # seconds
    deltas, shifts = get_deltas(period)
    if len(deltas) == period * 2:
        print("Noise too high, aborting")
        abort = True
    elif len(deltas) > period * 1.25:
        print("poor signal, another 48 secs...\n")
        period = 64
        deltas, shifts = get_deltas(period - 16)
    rc_signal.irq(handler=None)
    if not abort:
        num_deltas = len(deltas)
        while st_dev(deltas) > 5 and len(deltas) > 8: del_outlr(deltas)
        while st_dev(shifts) > 5 and len(shifts) > 8: del_outlr(shifts)
        if len(deltas) < len(shifts):
            deltas = [x for x in shifts]
            shift = 500
            print('shift = 500 ms')
        if num_deltas - len(deltas) > 0:
            print(num_deltas - len(deltas), "outliers removed")
        deltas.sort()
        vals = 'values = ['
        for x in deltas:
            vals += str(x+shift)+', '
        vals = vals[:-2] +']'
        print(vals)
        delta_t = round((mean(deltas) - shift)%1000)  # final %1000 in case shift makes delta_t < 0
        print(f'delay = {delta_t} ms')

############### Receive timecode ####################
# Know when to expect each timecode pulse now - sample timecode to detect pulse width
if not abort:
    yrs, mons, dys, mods = [],[],[],[]  # lists for received values
    # display lists to show progress
    dt = "Year:{}\nMonth:{}\nDay:{}\nMinute of day:{}"
    # Check if RTC is set to recent time
    if gmtime()[0] > 2021:
        # Pico hasn't cold started - use current rtc time for first set of values
        print('RTC time is', gmtime()[:5],'- using as first set')
        # MSF transmits GMT/BST for the coming minute
        year, month, day, hour, minute, _, _, _ = gmtime(time()) # type: ignore
        yrs, mons, dys, mods = [year%100], [month], [day], [hour*60 + minute+1]
    else:
        print('RTC time is', gmtime()[:5],' - cold start')

    # Indicators of confirmed parameters (year, month, day)
    match = [False]*3
    # Create registers for received timecode
    tcA = '                                             '  # need only the last 45 seconds
    tcB = '        '  # need just the parity bits

    # typically need 60-70 samples at 200 S/s to cover from the marker to bit B
    samples = array('B', (0 for _ in range(70)))  # samples[0] is for indexing
    elapsed = 0  # secs
    while True:
        # poll to trigger sampling at delta_t
        if ticks_ms()%1000 == delta_t:
            # trig.value(1)  # scope trigger
            # Set up timecode sampling
            sample = Timer(period=5, callback=isr_sample)
            while samples[0] < len(samples)-1:  # incremented by the interrupt
                pass  # keep sampling...
            # trig.value(0)  # scope trigger
            Timer.deinit(sample)
            samples[0] = 0
            if show_smpls:
                print(samples[A1:A2], samples[B1:B2])

            # Shift left and append new values, samples are averaged to reduce noise
            tcA = tcA[1:] + str(round(mean(samples[A1:A2])))
            tcB = tcB[1:] + str(round(mean(samples[B1:B2])))
            print(tcA)
            elapsed +=1  # seconds elapsed (for matching successive minute values)

            if tcA.endswith('01111110'):
                print('\nEnd of timecode')
                # Decode timecode
                # Year value
                # if not already found and if bits exist and pass odd parity check
                if not match[0] and tcA[-43] != ' ' and (tcA[-43:-35].count('1') + int(tcB[-6]))%2==1:
                    yr = int(tcA[-43:-39],2)*10 + int(tcA[-39:-35],2)  # decode BCD to decimal
                    if 23 < yr < 100:  # if value is sensible
                        yrs = yrs[-4:] + [yr]  # append to list & truncate to 5 values
                        if yrs.count(yr) == 3:  # when three values match accept value
                            year = 2000 + yr
                            match[0] = True
                            print("Match year!")
                # Month & day (share a parity bit)
                if tcA[-35] != ' ' and (tcA[-35:-24].count('1') + int(tcB[-5]))%2==1:
                    if not match[1]:
                        mon = int(tcA[-35],2)*10 + int(tcA[-34:-30],2)
                        if 0 < mon <= 12:
                            mons = mons[-4:] + [mon]
                            if mons.count(mon) == 3:
                                month = mon
                                match[1] = True
                                print("Match month!")
                    if not match[2]:
                        dy = int(tcA[-30:-28],2)*10 + int(tcA[-28:-24],2)
                        if 0 < dy <= 31:
                            dys = dys[-4:] + [dy]
                            if dys.count(dy) == 3:
                                day = dy
                                match[2] = True
                                print("Match day!")
                # Hour & minute (share a parity bit)
                # to allow rollover of hour, hour & minute is summed into minute_of_day
                # and are always determined last
                if tcA[-21] != ' ' and (tcA[-21:-8].count('1') + int(tcB[-3]))%2==1:
                    hr = int(tcA[-21:-19],2)*10 + int(tcA[-19:-15],2)
                    mn = int(tcA[-15:-12],2)*10 + int(tcA[-12:-8],2)
                    if hr < 24 and mn < 60:
                        mod = hr*60 + mn
                        # Update mods list to match to current minute
                        mods = [x + elapsed//60 for x in mods]
                        elapsed = 0
                        mods = mods[-4:] + [mod]
                        if mod <5:  # risk of day etc. having rolled over so restart matching
                            yrs = yrs[-1:]
                            mons = mons[-1:]
                            dys = dys[-1:]
                            match = [False]*3

                    # If all values match after two full sets of values, good confidence:
                    if (min([len(yrs), len(mons), len(dys)]) > 1 and
                        max(len(set(yrs)), len(set(mons)), len(set(dys))) == 1):
                            year, month, day = 2000+yr, mon, dy
                            match = [True, True, True]

                    # only set clock when current minute is found
                    if all(match) and ((len(mods)>1 and len(set(mods))==1) or mods.count(mod)>=3):
                        print(dt.format(yrs, mons, dys, mods))
                        print('match =',match)
                        hour, minute = hr, mn
                        RCtime = mktime((year, month, day, hour, minute, 0, 0,0,0))
                        # Check for summertime
                        fwd = mktime((year, 3, 31-(5*year//4+4)%7, 1, 0,0,0,0,0))
                        back = mktime((year, 10, 31-(5*year//4+1)%7, 2, 0,0,0,0,0)) ## in localtime
                        # For unlikely occurance of setting time in GMT/BST duplicated hour on the Autumn DST change (normally sync at 03:00)
                        if back-7200 < RCtime < back: break
                        #  otherwise:
                        utc = gmtime(RCtime - (3600 if fwd<=RCtime<back else 0))
                        # Set RTC as accurately as possible
                        while True:
                            if ticks_ms()%1000 == delta_t:
                                rtc.datetime((utc[0], utc[1], utc[2], 0, utc[3], utc[4], 0,0))
                                break
                        # print('set RTC:',year, month, day, hour, minute)
                        rc_sync = True
                        months = ("", "January", "February", "March", "April", "May", "June",
                        "July", "August", "September", "October", "November", "December")
                        print(f'RTC set to MSF (standard time) {gmtime()[2]} {months[gmtime()[1]]} {gmtime()[0]}  {gmtime()[3]:02.0f}:{gmtime()[4]:02.0f}')
                        break

                # display lists to show progress
                print(dt.format(yrs, mons, dys, mods))
                print('match =',match)

            if elapsed > timeout*60:
                print("Signal unreliable, aborting")
                break

if enable != 'off':
    rc_enable.value(0) if enable=='high' else rc_enable.value(1)
    # return(rc_sync, gmtime())