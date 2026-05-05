//*******************************************************************
//
//  Copyright (c) Light Illusion 2020 - All rights reserved.
//  This material contains confidential and proprietary information.
//  Unauthorized copying, in whole or part, is strictly prohibited.
//
//*******************************************************************


#include <iostream>
#include <sstream>
#include <sys/types.h>
#include <time.h>
#include <WinSock2.h>
#include <ws2tcpip.h>

#pragma comment(lib,"WS2_32")

using namespace std;


void msgMetric(char* buf, int rVal, int gVal, int bVal, int idx, double x, double y, double Y)
{
    ostringstream sout;
    sout << "<?xml version=\"1.0\" encoding=\"UTF-8\" ?>"       << endl; 
    sout << "<CS_RMC version=1>"                                << endl;
    sout << "    <metric>"                                      << endl;
    sout << "        <red>"   << rVal << "</red>"               << endl;
    sout << "        <green>" << gVal << "</green>"             << endl;
    sout << "        <blue>"  << bVal << "</blue>"              << endl;
    sout << "        <idx>"   << idx  << "</idx>"               << endl;
    sout << "        <x>"     << x << "</x>"                    << endl;
    sout << "        <y>"     << y << "</y>"                    << endl;
    sout << "        <Y>"     << Y << "</Y>"                    << endl;
    sout << "    </metric>"                                     << endl;
    sout << "</CS_RMC>"                                         << endl;

    strcpy(buf, sout.str().c_str());
}


void msgMeasure(char* buf, int rVal, int gVal, int bVal, int idx, int driftIdx = -1)
{
    ostringstream sout;
    sout << "<?xml version=\"1.0\" encoding=\"UTF-8\" ?>"       << endl; 
    sout << "<CS_RMC version=1>"                                << endl;
    if(driftIdx >= 0)
    sout << "    <measurement drift=\"" << driftIdx << "\">"    << endl;
    else
    sout << "    <measurement>"                                 << endl;
    sout << "        <red>"   << rVal << "</red>"               << endl;
    sout << "        <green>" << gVal << "</green>"             << endl;
    sout << "        <blue>"  << bVal << "</blue>"              << endl;
    sout << "        <idx>"   << idx  << "</idx>"               << endl;
    sout << "    </measurement>"                                << endl;
    sout << "</CS_RMC>"                                         << endl;

    strcpy(buf, sout.str().c_str());
}


void msgInit(char* buf, char* type)
{
    ostringstream sout;
    sout << "<?xml version=\"1.0\" encoding=\"UTF-8\" ?>"       << endl; 
    sout << "<CS_RMC version=1>"                                << endl;
    sout << "    <command>"                                     << endl;
    sout << "    init " << type                                 << endl;
    sout << "    </command>"                                    << endl;
    sout << "</CS_RMC>"                                         << endl;

    strcpy(buf, sout.str().c_str());
}


int
connect(string server, int port)
{   
    SOCKET sd = ::socket(PF_INET, SOCK_STREAM, 0);
    if(sd == -1) return -1;
    
    // get the host
    struct hostent *hostEnt = gethostbyname(server.c_str());
    if(!hostEnt)
    {
        WSACleanup();
        return -1;
    }
    
    // get the binary address
    struct sockaddr_in	inAddr;
    memset(&inAddr, 0x00, sizeof(inAddr));
    memcpy(&inAddr.sin_addr, hostEnt->h_addr_list[0], hostEnt->h_length);
    inAddr.sin_family = AF_INET;
    inAddr.sin_port = htons(port);

    // try to connect
    if (::connect(sd, (struct sockaddr *)&inAddr, sizeof(inAddr)) == -1)
    {
        WSACleanup();
        return -1;
    }


    /// wait here so the remote host has time to react
    Sleep(100);
    
    return (int)sd;
}


int
sendCmd(int sd, char* cmd, char* responce = 0)
{
    // the number of bytes in a command is sent as an int value, befor the actual command

    // find the lenght of the command
    int sz = strlen(cmd);
    // convert to network byte order
    int nSz = (int)htonl(sz);
    // send the size
    send(sd, (const char*)(&nSz), sizeof(int), 0);

    // now send the actual command
    int numWritten = 0;
    while(numWritten < sz)
    {
        int num = send(sd, cmd + numWritten, sz - numWritten, 0);
        numWritten += num;
    }
    // if no responce is required, return
    if(!responce) return numWritten;
    
    // read in one int, size of the following responce
    int readBufferSize(0);
    int numRead = 0;
    while(numRead < sizeof(int))
    {
        int num = recv(sd, ((char*)(&readBufferSize)) + numRead, sizeof(int) - numRead, 0);
        if(num == -1) return 0;
        numRead += num;
    }
     // convert to network byte order
    readBufferSize = ntohl(readBufferSize);
    
    
    // make the buffers
    int reqBufSize = readBufferSize ;
    char* reqBuf = new char[reqBufSize + 1];
    memset(reqBuf, 0x00, reqBufSize + 1);
    
    // read in the responce
    numRead = 0;
    while(numRead < reqBufSize)
    {
        int num = recv(sd, reqBuf + numRead, reqBufSize - numRead, 0);
        if(num == -1) return 0;
        numRead += num;
    }

    strcpy(responce, reqBuf);
    
    delete[] reqBuf;

    return numRead;
}

void parse_xyY(char* msg, double& x, double& y, double& Y)
{
    char* ptr;

    ptr = strstr(msg, "<x>");
    if(ptr)
    {
        ptr+=3;
        x = atof(ptr);
    }

    ptr = strstr(msg, "<y>");
    if(ptr)
    {
        ptr+=3;
        y = atof(ptr);
    }

    ptr = strstr(msg, "<Y>");
    if(ptr)
    {
        ptr+=3;
        Y = atof(ptr);
    }
}


int
main(int argc ,char** argv)
{
    // setup windows networking
    WSADATA winsockData;
    if(WSAStartup(MAKEWORD(2,2), &winsockData) != 0) return -1;
    
    // try to connect to localhost
    int sd = connect("localhost", 20102);
    if(sd == -1)
    {
        cout << "Error connecting to localhost:20102" << endl;
        return -1;
    }

    char reqBuf[2048];
    char rspBuf[2048];

    //  send the init command, this clears out any existing data in the connected Colour Space profile window
    msgInit(reqBuf,"profile");
    sendCmd(sd, reqBuf);
    cout << "Sennding Initalisation command" << endl;
    cout << reqBuf << endl;
    cout << endl;

    // measure a 3x3x3 cube
    int sz(3);
    int ct(1);
    for(int rc(0); rc < sz; ++rc )
    {
        for(int gc(0); gc < sz; ++gc )
        {
            for(int bc(0); bc < sz; ++bc )
            {
                int ri, gi, bi;

                ri = (int)((((double)rc / (double)(sz - 1)) * 255.0) + 0.5);
                gi = (int)((((double)gc / (double)(sz - 1)) * 255.0) + 0.5);
                bi = (int)((((double)bc / (double)(sz - 1)) * 255.0) + 0.5);
                
                cout << "Sending measure patch " << ct << " (" << ri << "," << gi << "," << bi << "," << ct << ")" << endl;
                memset(rspBuf, 2048, 0x00);
                msgMeasure(reqBuf, ri, gi, bi, ct);
                cout << reqBuf << endl;

                // Send a patch to measure, wait for the responce
                sendCmd(sd, reqBuf, rspBuf);
                cout << "Results returned" << endl;
                cout << rspBuf << endl;

                // parse the result
                double rY(0), rx(0), ry(0);
                parse_xyY(rspBuf, rx, ry, rY);

                // Send a metric request
                cout << "Sending for metrics (" << rx << "," << ry << "," << rY << ")" << endl;
                memset(rspBuf, 2048, 0x00);
                msgMetric(reqBuf, ri, gi, bi, ct, rx, ry, rY);
                cout << reqBuf << endl;
                sendCmd(sd, reqBuf, rspBuf);
                cout << "Metrics returned" << endl;
                cout << rspBuf << endl;
                cout << endl << endl;

                ++ct;
            }
        }
    }

    // close down windows networking
    WSACleanup();
    
    return 0;
}
